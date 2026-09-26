#!/usr/bin/env python3
"""Exercise the MCP server against a real KDE Wayland desktop.

The scenario launches a disposable Konsole window, discovers and activates it
through MCP, focuses it with an absolute-coordinate click, types a mixed-case
value containing punctuation, submits it, captures the result, and runs OCR.
It writes a JSON report and the before/after screenshots to an artifact folder.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts/kde-wayland-e2e"),
        help="directory for report.json and screenshot evidence",
    )
    parser.add_argument(
        "--ei-helper",
        type=Path,
        help="override computer-control-kwin-ei (useful for testing a fresh build)",
    )
    return parser.parse_args()


def text_items(result: Any) -> list[str]:
    return [item.text for item in result.content if item.type == "text"]


def json_items(result: Any) -> list[Any]:
    return [json.loads(text) for text in text_items(result)]


def save_image(result: Any, destination: Path) -> str:
    image = next((item for item in result.content if item.type == "image"), None)
    if image is None:
        raise AssertionError(f"MCP result did not contain an image: {result}")
    data = base64.b64decode(image.data)
    destination.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


async def wait_for_window(
    session: ClientSession, title: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await session.call_tool("list_windows", {})
        for window in json_items(result):
            if title in window.get("title", ""):
                return window
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Konsole window did not appear within {timeout:.1f}s: {title}"
    )


async def wait_for_receipt(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8").rstrip("\n")
        await asyncio.sleep(0.1)
    raise AssertionError(f"typed input was not received within {timeout:.1f}s")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        raise RuntimeError("this E2E scenario requires a Wayland session")
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        raise RuntimeError("this E2E scenario requires KDE Plasma")

    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = artifact_dir / "typed-value.txt"
    receipt_path.unlink(missing_ok=True)

    title = f"computer-control-mcp-e2e-{os.getpid()}"
    typed_value = "Fedora44-KDE_Wayland!"
    shell_program = (
        "printf 'MCP E2E READY\\n'; "
        "IFS= read -r value; "
        'printf \'%s\\n\' "$value" > "$1"; '
        "printf 'E2E RECEIVED: %s\\n' \"$value\"; "
        "sleep 30"
    )
    konsole = await asyncio.create_subprocess_exec(
        "konsole",
        "--separate",
        "--hide-menubar",
        "--hide-tabbar",
        "--notransparency",
        "-p",
        f"LocalTabTitleFormat={title}",
        "-e",
        "bash",
        "--noprofile",
        "--norc",
        "-c",
        shell_program,
        "e2e-shell",
        str(receipt_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    report: dict[str, Any] = {
        "scenario": "KDE Wayland MCP input, capture, and OCR",
        "status": "running",
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
            "xdg_current_desktop": os.environ.get("XDG_CURRENT_DESKTOP"),
            "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        },
        "target_title": title,
        "typed_value": typed_value,
    }
    window_id: str | None = None

    try:
        server_environment = os.environ.copy()
        if args.ei_helper:
            server_environment["COMPUTER_CONTROL_MCP_EI_HELPER"] = str(
                args.ei_helper.resolve()
            )
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "computer_control_mcp", "server"],
            env=server_environment,
        )

        async with stdio_client(server) as (read_stream, write_stream):  # noqa: SIM117
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                required_tools = {
                    "activate_window",
                    "click_screen",
                    "get_screen_size",
                    "list_windows",
                    "press_keys",
                    "take_screenshot",
                    "take_screenshot_with_ocr",
                    "type_text",
                }
                missing_tools = sorted(required_tools.difference(tool_names))
                if missing_tools:
                    raise AssertionError(
                        f"server is missing MCP tools: {missing_tools}"
                    )

                report["server"] = {
                    "name": initialized.serverInfo.name,
                    "version": initialized.serverInfo.version,
                    "tools": tool_names,
                }
                screen_result = await session.call_tool("get_screen_size", {})
                report["screen"] = json_items(screen_result)[0]

                window = await wait_for_window(session, title)
                window_id = window["window_id"]
                report["window"] = window

                activate_result = await session.call_tool(
                    "activate_window",
                    {"title_pattern": title, "threshold": 100},
                )
                activate_text = "\n".join(text_items(activate_result))
                if "Successfully activated" not in activate_text:
                    raise AssertionError(activate_text)
                report["activate_result"] = activate_text

                before_result = await session.call_tool(
                    "take_screenshot",
                    {"title_pattern": title, "threshold": 100},
                )
                report["before_sha256"] = save_image(
                    before_result, artifact_dir / "before.png"
                )

                click_x = window["left"] + window["width"] // 2
                click_y = window["top"] + window["height"] // 2
                click_result = await session.call_tool(
                    "click_screen", {"x": click_x, "y": click_y}
                )
                click_text = "\n".join(text_items(click_result))
                if "Successfully clicked" not in click_text:
                    raise AssertionError(click_text)
                report["click"] = {"x": click_x, "y": click_y, "result": click_text}

                type_result = await session.call_tool(
                    "type_text", {"text": typed_value}
                )
                type_text_result = "\n".join(text_items(type_result))
                if "Successfully typed" not in type_text_result:
                    raise AssertionError(type_text_result)

                enter_result = await session.call_tool("press_keys", {"keys": "enter"})
                enter_text = "\n".join(text_items(enter_result))
                if "Pressed single key" not in enter_text:
                    raise AssertionError(enter_text)

                received = await wait_for_receipt(receipt_path)
                if received != typed_value:
                    raise AssertionError(
                        f"terminal received {received!r}, expected {typed_value!r}"
                    )
                report["received_value"] = received
                report["type_result"] = type_text_result
                report["enter_result"] = enter_text

                after_result = await session.call_tool(
                    "take_screenshot",
                    {"title_pattern": title, "threshold": 100},
                )
                report["after_sha256"] = save_image(
                    after_result, artifact_dir / "after.png"
                )

                ocr_result = await session.call_tool(
                    "take_screenshot_with_ocr",
                    {"title_pattern": title, "threshold": 100},
                )
                ocr_text = "\n".join(text_items(ocr_result))
                (artifact_dir / "ocr.txt").write_text(ocr_text, encoding="utf-8")
                if "E2E" not in ocr_text and "Fedora44" not in ocr_text:
                    raise AssertionError(f"OCR did not find the E2E output: {ocr_text}")
                report["ocr_contains_e2e_output"] = True

        report["status"] = "passed"
        return report
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        if window_id:
            close_window = await asyncio.create_subprocess_exec(
                "kdotool",
                "windowclose",
                window_id,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await close_window.wait()
        if konsole.returncode is None:
            konsole.terminate()
            try:
                await asyncio.wait_for(konsole.wait(), timeout=2)
            except TimeoutError:
                konsole.kill()
                await asyncio.wait_for(konsole.wait(), timeout=2)
        report_path = artifact_dir / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    args = parse_args()
    try:
        report = asyncio.run(run(args))
    except Exception:
        print(f"E2E failed; evidence: {args.artifact_dir.resolve()}", file=sys.stderr)
        raise
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"Evidence: {args.artifact_dir.resolve()}")


if __name__ == "__main__":
    main()
