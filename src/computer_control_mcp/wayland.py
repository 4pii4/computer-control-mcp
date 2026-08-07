import ast
import atexit
from difflib import SequenceMatcher
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image as PILImage


_LETTER_KEYCODES = {
    letter: code
    for letter, code in zip(
        "abcdefghijklmnopqrstuvwxyz",
        [
            30,
            48,
            46,
            32,
            18,
            33,
            34,
            35,
            23,
            36,
            37,
            38,
            50,
            49,
            24,
            25,
            16,
            19,
            31,
            20,
            22,
            47,
            17,
            45,
            21,
            44,
        ],
    )
}
_KEYCODES = {
    **_LETTER_KEYCODES,
    "1": 2,
    "2": 3,
    "3": 4,
    "4": 5,
    "5": 6,
    "6": 7,
    "7": 8,
    "8": 9,
    "9": 10,
    "0": 11,
    "esc": 1,
    "escape": 1,
    "backspace": 14,
    "tab": 15,
    "enter": 28,
    "return": 28,
    "ctrl": 29,
    "leftctrl": 29,
    "shift": 42,
    "leftshift": 42,
    "rightshift": 54,
    "alt": 56,
    "leftalt": 56,
    "space": 57,
    "capslock": 58,
    "f1": 59,
    "f2": 60,
    "f3": 61,
    "f4": 62,
    "f5": 63,
    "f6": 64,
    "f7": 65,
    "f8": 66,
    "f9": 67,
    "f10": 68,
    "numlock": 69,
    "scrolllock": 70,
    "f11": 87,
    "f12": 88,
    "rightctrl": 97,
    "rightalt": 100,
    "home": 102,
    "up": 103,
    "pageup": 104,
    "pgup": 104,
    "left": 105,
    "right": 106,
    "end": 107,
    "down": 108,
    "pagedown": 109,
    "pgdn": 109,
    "insert": 110,
    "delete": 111,
    "del": 111,
    "meta": 125,
    "super": 125,
    "win": 125,
    "command": 125,
}
_SHIFTED_CHARACTERS = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
}
_PUNCTUATION_KEYCODES = {
    "-": (12, False),
    "_": (12, True),
    "=": (13, False),
    "+": (13, True),
    "[": (26, False),
    "{": (26, True),
    "]": (27, False),
    "}": (27, True),
    ";": (39, False),
    ":": (39, True),
    "'": (40, False),
    '"': (40, True),
    "`": (41, False),
    "~": (41, True),
    "\\": (43, False),
    "|": (43, True),
    ",": (51, False),
    "<": (51, True),
    ".": (52, False),
    ">": (52, True),
    "/": (53, False),
    "?": (53, True),
}
_EI_PROCESS = None
_EI_LOCK = threading.Lock()


def is_wayland_session(environment=None) -> bool:
    environment = os.environ if environment is None else environment
    return environment.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
        environment.get("WAYLAND_DISPLAY")
    )


def _run(command, **kwargs) -> str:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        **kwargs,
    )
    return result.stdout


def _ei_helper_path() -> str:
    configured = os.getenv("COMPUTER_CONTROL_MCP_EI_HELPER")
    if configured:
        return configured
    helper = shutil.which("computer-control-kwin-ei")
    if not helper:
        raise RuntimeError(
            "computer-control-kwin-ei is not installed; run scripts/setup-wayland-fedora.sh"
        )
    return helper


def _stop_ei_helper() -> None:
    global _EI_PROCESS
    if _EI_PROCESS and _EI_PROCESS.poll() is None:
        try:
            _EI_PROCESS.stdin.write("quit\n")
            _EI_PROCESS.stdin.flush()
            _EI_PROCESS.wait(timeout=1)
        except Exception:
            _EI_PROCESS.terminate()
    _EI_PROCESS = None


atexit.register(_stop_ei_helper)


def _ei_command(*parts: str) -> None:
    global _EI_PROCESS
    with _EI_LOCK:
        if _EI_PROCESS is None or _EI_PROCESS.poll() is not None:
            _EI_PROCESS = subprocess.Popen(
                [_ei_helper_path()],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            if _EI_PROCESS.stdout.readline().strip() != "ready":
                _stop_ei_helper()
                raise RuntimeError("KWin EIS helper failed to initialize")

        _EI_PROCESS.stdin.write(" ".join(parts) + "\n")
        _EI_PROCESS.stdin.flush()
        if _EI_PROCESS.stdout.readline().strip() != "ok":
            raise RuntimeError(f"KWin EIS command failed: {' '.join(parts)}")


def _logical_screen_size() -> Tuple[int, int]:
    configuration = json.loads(_run(["kscreen-doctor", "--json"]))
    current_size = configuration["screen"]["currentSize"]
    return int(current_size["width"]), int(current_size["height"])


def get_screen_size() -> Tuple[int, int]:
    return _logical_screen_size()


def click_screen(x: int, y: int) -> None:
    _ei_command("click", str(x), str(y), "0")


def move_mouse(x: int, y: int) -> None:
    _ei_command("move", str(x), str(y))


def _mouse_button_number(button: str) -> int:
    buttons = {"left": 0, "right": 1, "middle": 2}
    normalized = button.lower()
    if normalized not in buttons:
        raise ValueError(f"Unsupported mouse button: {button}")
    return buttons[normalized]


def mouse_down(button: str = "left") -> None:
    _ei_command("button", str(_mouse_button_number(button)), "1")


def mouse_up(button: str = "left") -> None:
    _ei_command("button", str(_mouse_button_number(button)), "0")


def drag_mouse(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    duration: float = 0.5,
) -> None:
    move_mouse(from_x, from_y)
    mouse_down()
    try:
        steps = max(1, int(duration * 60)) if duration > 0 else 1
        for step in range(1, steps + 1):
            progress = step / steps
            x = round(from_x + (to_x - from_x) * progress)
            y = round(from_y + (to_y - from_y) * progress)
            move_mouse(x, y)
            if duration > 0:
                time.sleep(duration / steps)
    finally:
        mouse_up()


def _keycode(key: str) -> int:
    normalized = key.lower()
    if normalized not in _KEYCODES:
        raise ValueError(f"Unsupported key on Wayland: {key}")
    return _KEYCODES[normalized]


def _send_keycodes(events) -> None:
    for event in events:
        code, state = event.split(":", 1)
        _ei_command("key", code, state)


def type_text(text: str) -> None:
    for character in text:
        if character.isalpha() and character.isascii():
            code = _keycode(character.lower())
            shifted = character.isupper()
        elif character.isdigit():
            code = _keycode(character)
            shifted = False
        elif character in _SHIFTED_CHARACTERS:
            code = _keycode(_SHIFTED_CHARACTERS[character])
            shifted = True
        elif character in _PUNCTUATION_KEYCODES:
            code, shifted = _PUNCTUATION_KEYCODES[character]
        elif character == " ":
            code, shifted = _keycode("space"), False
        elif character == "\n":
            code, shifted = _keycode("enter"), False
        elif character == "\t":
            code, shifted = _keycode("tab"), False
        else:
            raise ValueError(f"Unsupported character on Wayland: {character!r}")

        if shifted:
            key_down("shift")
        _send_keycodes([f"{code}:1", f"{code}:0"])
        if shifted:
            key_up("shift")


def key_down(key: str) -> None:
    _send_keycodes([f"{_keycode(key)}:1"])


def key_up(key: str) -> None:
    _send_keycodes([f"{_keycode(key)}:0"])


def press_keys(keys) -> None:
    items = [keys] if isinstance(keys, str) else keys
    for item in items:
        if isinstance(item, str):
            code = _keycode(item)
            _send_keycodes([f"{code}:1", f"{code}:0"])
        elif isinstance(item, list):
            codes = [_keycode(key) for key in item]
            events = [f"{code}:1" for code in codes]
            events.extend(f"{code}:0" for code in reversed(codes))
            _send_keycodes(events)
        else:
            raise ValueError(f"Invalid key format: {item}")


def _variant_value(output: str, key: str):
    match = re.search(
        rf"'{re.escape(key)}': <(?P<value>'(?:\\.|[^'])*'|true|false|-?\d+(?:\.\d+)?)>",
        output,
    )
    if not match:
        return None

    value = match.group("value")
    if value.startswith("'"):
        return ast.literal_eval(value)
    if value in {"true", "false"}:
        return value == "true"
    return float(value) if "." in value else int(value)


def list_windows() -> List[Dict[str, Any]]:
    window_ids = [
        window_id
        for window_id in _run(["kdotool", "search", "--title", ".*"]).splitlines()
        if window_id
    ]
    active_window_id = _run(["kdotool", "getactivewindow", "getwindowid"]).strip()
    windows = []

    for window_id in window_ids:
        metadata = _run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.kde.KWin",
                "--object-path",
                "/KWin",
                "--method",
                "org.kde.KWin.getWindowInfo",
                window_id,
            ]
        )
        title = _variant_value(metadata, "caption")
        if not title:
            continue

        minimized = bool(_variant_value(metadata, "minimized"))
        windows.append(
            {
                "title": title,
                "left": int(_variant_value(metadata, "x") or 0),
                "top": int(_variant_value(metadata, "y") or 0),
                "width": int(_variant_value(metadata, "width") or 0),
                "height": int(_variant_value(metadata, "height") or 0),
                "is_active": window_id == active_window_id,
                "is_visible": not minimized,
                "is_minimized": minimized,
                "is_maximized": bool(
                    _variant_value(metadata, "maximizeHorizontal")
                    and _variant_value(metadata, "maximizeVertical")
                ),
                "window_id": window_id,
            }
        )

    return windows


def _find_matching_window(
    windows: List[Dict[str, Any]],
    title_pattern: str,
    use_regex: bool = False,
    threshold: int = 60,
):
    if use_regex:
        return next(
            (
                window
                for window in windows
                if re.search(title_pattern, window["title"], re.IGNORECASE)
            ),
            None,
        )

    pattern = title_pattern.casefold()
    best_window = None
    best_score = -1
    for window in windows:
        title = window["title"].casefold()
        score = (
            100
            if pattern in title
            else int(SequenceMatcher(None, pattern, title).ratio() * 100)
        )
        if score > best_score:
            best_window = window
            best_score = score
    return best_window if best_score >= threshold else None


def activate_window(
    title_pattern: str, use_regex: bool = False, threshold: int = 60
) -> str:
    window = _find_matching_window(list_windows(), title_pattern, use_regex, threshold)
    if not window:
        raise ValueError(f"No window found matching pattern: {title_pattern}")
    _run(["kdotool", "windowactivate", window["window_id"]])
    return window["title"]


def take_screenshot(
    title_pattern: Optional[str] = None,
    use_regex: bool = False,
    threshold: int = 10,
    temp_dir: Optional[Path] = None,
) -> PILImage.Image:
    owns_temp_dir = temp_dir is None
    screenshot_dir = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp())
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / "wayland-screenshot.png"

    try:
        capture_mode = "--fullscreen"
        if title_pattern:
            window = _find_matching_window(
                list_windows(), title_pattern, use_regex, threshold
            )
            if not window:
                raise ValueError(f"No window found matching pattern: {title_pattern}")
            _run(["kdotool", "windowactivate", window["window_id"]])
            time.sleep(0.2)
            capture_mode = "--activewindow"
            logical_size = (window["width"], window["height"])
        else:
            logical_size = _logical_screen_size()

        _run(
            [
                "spectacle",
                "--background",
                "--nonotify",
                capture_mode,
                "--output",
                str(screenshot_path),
            ]
        )
        with PILImage.open(screenshot_path) as captured:
            screenshot = captured.convert("RGB").copy()

        if screenshot.size != logical_size:
            screenshot = screenshot.resize(logical_size, PILImage.Resampling.LANCZOS)
        return screenshot
    finally:
        if owns_temp_dir:
            shutil.rmtree(screenshot_dir, ignore_errors=True)
