import json
from pathlib import Path

from PIL import Image

from computer_control_mcp import wayland


def test_wayland_detection_accepts_session_type_or_display_name():
    assert wayland.is_wayland_session({"XDG_SESSION_TYPE": "wayland"})
    assert wayland.is_wayland_session({"WAYLAND_DISPLAY": "wayland-0"})
    assert not wayland.is_wayland_session({"XDG_SESSION_TYPE": "x11"})


def test_take_screenshot_scales_to_logical_desktop_size(tmp_path, monkeypatch):
    logical_size = {"screen": {"currentSize": {"width": 1536, "height": 864}}}

    def fake_run(command, **kwargs):
        if command[:2] == ["kscreen-doctor", "--json"]:
            return json.dumps(logical_size)
        if command[0] == "spectacle":
            output_path = Path(command[command.index("--output") + 1])
            Image.new("RGB", (1920, 1080), "red").save(output_path)
            return ""
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(wayland, "_run", fake_run)

    screenshot = wayland.take_screenshot(temp_dir=tmp_path)

    assert screenshot.size == (1536, 864)


def test_click_uses_kwin_eis_logical_coordinates(monkeypatch):
    commands = []
    monkeypatch.setattr(
        wayland,
        "_ei_command",
        lambda *command: commands.append(command),
        raising=False,
    )
    monkeypatch.setattr(
        wayland,
        "_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pointer input must not use ydotool")
        ),
    )

    wayland.click_screen(320, 240)

    assert commands == [("click", "320", "240", "0")]


def test_pointer_hold_release_and_drag_use_kwin_eis(monkeypatch):
    commands = []
    monkeypatch.setattr(
        wayland,
        "_ei_command",
        lambda *command: commands.append(command),
        raising=False,
    )
    monkeypatch.setattr(wayland.time, "sleep", lambda _seconds: None)

    wayland.move_mouse(10, 20)
    wayland.mouse_down("right")
    wayland.mouse_up("right")
    wayland.drag_mouse(1, 2, 3, 4, duration=0)

    assert commands == [
        ("move", "10", "20"),
        ("button", "1", "1"),
        ("button", "1", "0"),
        ("move", "1", "2"),
        ("button", "0", "1"),
        ("move", "3", "4"),
        ("button", "0", "0"),
    ]


def test_press_keys_maps_names_and_combinations_to_linux_keycodes(monkeypatch):
    commands = []
    monkeypatch.setattr(
        wayland,
        "_ei_command",
        lambda *command: commands.append(command),
        raising=False,
    )

    wayland.press_keys(["a", ["ctrl", "c"], "enter"])

    assert commands == [
        ("key", "30", "1"),
        ("key", "30", "0"),
        ("key", "29", "1"),
        ("key", "46", "1"),
        ("key", "46", "0"),
        ("key", "29", "0"),
        ("key", "28", "1"),
        ("key", "28", "0"),
    ]


def test_text_and_key_hold_use_kwin_eis(monkeypatch):
    commands = []
    monkeypatch.setattr(
        wayland,
        "_ei_command",
        lambda *command: commands.append(command),
        raising=False,
    )

    wayland.type_text("hello")
    wayland.key_down("ctrl")
    wayland.key_up("ctrl")

    assert commands == [
        ("key", "35", "1"),
        ("key", "35", "0"),
        ("key", "18", "1"),
        ("key", "18", "0"),
        ("key", "38", "1"),
        ("key", "38", "0"),
        ("key", "38", "1"),
        ("key", "38", "0"),
        ("key", "24", "1"),
        ("key", "24", "0"),
        ("key", "29", "1"),
        ("key", "29", "0"),
    ]


def test_list_windows_uses_kwin_metadata(monkeypatch):
    window_id = "{11111111-2222-3333-4444-555555555555}"
    window_info = (
        "({'caption': <'Native Wayland App'>, 'fullscreen': <false>, "
        "'height': <700.0>, 'maximizeHorizontal': <2>, "
        "'maximizeVertical': <1>, 'minimized': <false>, "
        "'uuid': <'{11111111-2222-3333-4444-555555555555}'>, "
        "'width': <1200.0>, 'x': <40.0>, 'y': <60.0>},)"
    )

    def fake_run(command, **kwargs):
        if command == ["kdotool", "search", "--title", ".*"]:
            return f"{window_id}\n"
        if command == ["kdotool", "getactivewindow", "getwindowid"]:
            return f"{window_id}\n"
        if command[-2:] == ["org.kde.KWin.getWindowInfo", window_id]:
            return window_info
        raise AssertionError(f"Unexpected command: {command}")

    monkeypatch.setattr(wayland, "_run", fake_run)

    assert wayland.list_windows() == [
        {
            "title": "Native Wayland App",
            "left": 40,
            "top": 60,
            "width": 1200,
            "height": 700,
            "is_active": True,
            "is_visible": True,
            "is_minimized": False,
            "is_maximized": True,
            "window_id": window_id,
        }
    ]


def test_activate_window_uses_matching_kwin_window_id(monkeypatch):
    windows = [
        {"title": "Settings", "window_id": "{settings}"},
        {"title": "Native Wayland App", "window_id": "{native}"},
    ]
    commands = []
    monkeypatch.setattr(wayland, "list_windows", lambda: windows)
    monkeypatch.setattr(
        wayland,
        "_run",
        lambda command, **kwargs: commands.append(command) or "",
    )

    title = wayland.activate_window("native wayland")

    assert title == "Native Wayland App"
    assert commands == [["kdotool", "windowactivate", "{native}"]]


def test_take_window_screenshot_uses_active_window_and_logical_geometry(
    tmp_path, monkeypatch
):
    commands = []
    monkeypatch.setattr(
        wayland,
        "list_windows",
        lambda: [
            {
                "title": "Native Wayland App",
                "window_id": "{native}",
                "width": 800,
                "height": 640,
            }
        ],
    )
    monkeypatch.setattr(wayland.time, "sleep", lambda _seconds: None)

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "spectacle":
            output_path = Path(command[command.index("--output") + 1])
            Image.new("RGB", (1000, 800), "blue").save(output_path)
        return ""

    monkeypatch.setattr(wayland, "_run", fake_run)

    screenshot = wayland.take_screenshot(title_pattern="native", temp_dir=tmp_path)

    assert screenshot.size == (800, 640)
    assert commands[0] == ["kdotool", "windowactivate", "{native}"]
    assert "--activewindow" in commands[1]
