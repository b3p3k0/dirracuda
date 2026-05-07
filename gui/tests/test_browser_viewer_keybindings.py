"""Phase 2 browser/viewer keyboard shortcut behavior tests."""

from __future__ import annotations

from typing import Callable, Dict

import pytest

from gui.components.file_viewer_window import FileViewerWindow
from gui.components.image_viewer_window import ImageViewerWindow
from gui.components.unified_browser_window import (
    FtpBrowserWindow,
    HttpBrowserWindow,
    SmbBrowserWindow,
)


class _BindRecorder:
    def __init__(self) -> None:
        self.bindings: Dict[str, Callable] = {}

    def bind(self, sequence: str, callback: Callable) -> None:
        self.bindings[sequence] = callback


@pytest.mark.parametrize("browser_cls", [FtpBrowserWindow, HttpBrowserWindow, SmbBrowserWindow])
def test_browser_shortcuts_dispatch_expected_actions(browser_cls) -> None:
    calls = {"open": 0, "up": 0, "refresh": 0, "close": 0}
    window = _BindRecorder()
    tree = _BindRecorder()

    browser = browser_cls.__new__(browser_cls)
    browser.window = window
    browser.tree = tree
    browser._on_item_double_click = lambda _event=None: calls.__setitem__("open", calls["open"] + 1)
    browser._on_up = lambda: calls.__setitem__("up", calls["up"] + 1)
    browser._refresh = lambda: calls.__setitem__("refresh", calls["refresh"] + 1)
    browser._on_close = lambda: calls.__setitem__("close", calls["close"] + 1)

    browser._bind_keyboard_shortcuts()

    assert tree.bindings["<Return>"](None) == "break"
    assert window.bindings["<BackSpace>"](None) == "break"
    assert window.bindings["<Alt-Up>"](None) == "break"
    assert window.bindings["<F5>"](None) == "break"
    assert window.bindings["<Control-r>"](None) == "break"
    assert window.bindings["<Escape>"](None) == "break"
    assert window.bindings["<Control-w>"](None) == "break"

    assert calls == {"open": 1, "up": 2, "refresh": 2, "close": 2}


def test_file_viewer_shortcuts_close_only_without_save() -> None:
    calls = {"close": 0, "save": 0}
    viewer = FileViewerWindow.__new__(FileViewerWindow)
    viewer.window = _BindRecorder()
    viewer.on_save_callback = None
    viewer._on_close = lambda: calls.__setitem__("close", calls["close"] + 1)
    viewer._on_save = lambda: calls.__setitem__("save", calls["save"] + 1)

    viewer._bind_keyboard_shortcuts()

    assert viewer.window.bindings["<Escape>"](None) == "break"
    assert "<Control-s>" not in viewer.window.bindings
    assert calls == {"close": 1, "save": 0}


def test_file_viewer_shortcuts_include_save_when_available() -> None:
    calls = {"close": 0, "save": 0}
    viewer = FileViewerWindow.__new__(FileViewerWindow)
    viewer.window = _BindRecorder()
    viewer.on_save_callback = object()
    viewer._on_close = lambda: calls.__setitem__("close", calls["close"] + 1)
    viewer._on_save = lambda: calls.__setitem__("save", calls["save"] + 1)

    viewer._bind_keyboard_shortcuts()

    assert viewer.window.bindings["<Control-s>"](None) == "break"
    assert viewer.window.bindings["<Escape>"](None) == "break"
    assert calls == {"close": 1, "save": 1}


def test_image_viewer_shortcuts_close_only_without_save() -> None:
    calls = {"close": 0, "save": 0}
    viewer = ImageViewerWindow.__new__(ImageViewerWindow)
    viewer.window = _BindRecorder()
    viewer.on_save_callback = None
    viewer._on_close = lambda: calls.__setitem__("close", calls["close"] + 1)
    viewer._on_save = lambda: calls.__setitem__("save", calls["save"] + 1)

    viewer._bind_keyboard_shortcuts()

    assert viewer.window.bindings["<Escape>"](None) == "break"
    assert "<Control-s>" not in viewer.window.bindings
    assert calls == {"close": 1, "save": 0}


def test_image_viewer_shortcuts_include_save_when_available() -> None:
    calls = {"close": 0, "save": 0}
    viewer = ImageViewerWindow.__new__(ImageViewerWindow)
    viewer.window = _BindRecorder()
    viewer.on_save_callback = object()
    viewer._on_close = lambda: calls.__setitem__("close", calls["close"] + 1)
    viewer._on_save = lambda: calls.__setitem__("save", calls["save"] + 1)

    viewer._bind_keyboard_shortcuts()

    assert viewer.window.bindings["<Control-s>"](None) == "break"
    assert viewer.window.bindings["<Escape>"](None) == "break"
    assert calls == {"close": 1, "save": 1}
