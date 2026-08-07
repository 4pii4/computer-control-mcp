from computer_control_mcp import core
import pytest
from PIL import Image as PILImage


def test_basic_tools_dispatch_to_wayland_backend(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core.wayland,
        "click_screen",
        lambda x, y: calls.append(("click", x, y)),
    )
    monkeypatch.setattr(core.wayland, "get_screen_size", lambda: (1536, 864))
    monkeypatch.setattr(
        core.wayland,
        "list_windows",
        lambda: [{"title": "Native Wayland App"}],
    )
    monkeypatch.setattr(
        core.wayland,
        "activate_window",
        lambda pattern, use_regex, threshold: "Native Wayland App",
    )

    assert core.IS_WAYLAND is True
    assert core.click_screen(12, 34) == "Successfully clicked at coordinates (12, 34)"
    assert core.get_screen_size() == {
        "width": 1536,
        "height": 864,
        "message": "Screen size: 1536x864",
    }
    assert core.list_windows() == [{"title": "Native Wayland App"}]
    assert (
        core.activate_window("native")
        == "Successfully activated window: 'Native Wayland App'"
    )
    assert calls == [("click", 12, 34)]


@pytest.mark.asyncio
async def test_input_tools_dispatch_to_wayland_backend(monkeypatch):
    calls = []
    monkeypatch.setattr(
        core.wayland,
        "type_text",
        lambda text: calls.append(("type", text)),
    )
    monkeypatch.setattr(
        core.wayland,
        "move_mouse",
        lambda x, y: calls.append(("move", x, y)),
    )
    monkeypatch.setattr(
        core.wayland,
        "mouse_down",
        lambda button: calls.append(("down", button)),
    )
    monkeypatch.setattr(
        core.wayland,
        "mouse_up",
        lambda button: calls.append(("up", button)),
    )
    monkeypatch.setattr(
        core.wayland,
        "drag_mouse",
        lambda *args: calls.append(("drag", *args)),
    )
    monkeypatch.setattr(
        core.wayland,
        "key_down",
        lambda key: calls.append(("key_down", key)),
    )
    monkeypatch.setattr(
        core.wayland,
        "key_up",
        lambda key: calls.append(("key_up", key)),
    )
    monkeypatch.setattr(
        core.wayland,
        "press_keys",
        lambda keys: calls.append(("press", keys)),
    )

    assert core.type_text("hello") == "Successfully typed text: hello"
    assert core.move_mouse(10, 20) == "Successfully moved mouse to coordinates (10, 20)"
    assert core.mouse_down("right") == "Held down right mouse button"
    assert core.mouse_up("right") == "Released right mouse button"
    assert (
        await core.drag_mouse(1, 2, 3, 4, 0.25)
        == "Successfully dragged from (1, 2) to (3, 4)"
    )
    assert core.key_down("ctrl") == "Held down key: ctrl"
    assert core.key_up("ctrl") == "Released key: ctrl"
    assert core.press_keys([["ctrl", "c"]]).startswith(
        "Successfully pressed keys sequence"
    )
    assert calls == [
        ("type", "hello"),
        ("move", 10, 20),
        ("down", "right"),
        ("up", "right"),
        ("drag", 1, 2, 3, 4, 0.25),
        ("key_down", "ctrl"),
        ("key_up", "ctrl"),
        ("press", [["ctrl", "c"]]),
    ]


def test_screenshot_tool_uses_wayland_capture(monkeypatch):
    calls = []

    def fake_screenshot(**kwargs):
        calls.append(kwargs)
        return PILImage.new("RGB", (1536, 864), "green")

    monkeypatch.setattr(core.wayland, "take_screenshot", fake_screenshot)

    result = core.take_screenshot(
        title_pattern="native",
        use_regex=True,
        threshold=75,
        save_to_downloads=False,
    )

    assert PILImage.open(result.path).size == (1536, 864)
    assert calls == [
        {
            "title_pattern": "native",
            "use_regex": True,
            "threshold": 75,
        }
    ]


def test_ocr_tool_uses_wayland_capture(monkeypatch):
    calls = []

    class Box:
        def tolist(self):
            return [[1, 2], [3, 2], [3, 4], [1, 4]]

    class OcrOutput:
        boxes = [Box()]
        txts = ["Hello"]
        scores = [0.95]

    def fake_screenshot(**kwargs):
        calls.append(kwargs)
        return PILImage.new("RGB", (100, 50), "white")

    monkeypatch.setattr(core.wayland, "take_screenshot", fake_screenshot)
    monkeypatch.setattr(core, "engine", lambda _image: OcrOutput())

    result = core.take_screenshot_with_ocr(title_pattern="native")

    assert "Hello" in result
    assert calls == [
        {
            "title_pattern": "native",
            "use_regex": False,
            "threshold": 10,
        }
    ]
