"""C5 — Pre-Decode Image Pixel Guard regression tests.

Proves ImageViewerWindow._load_image_safe inspects dimensions after Pillow's
lazy open() and before load(), rejecting zero/negative/malformed/over-limit
images without ever triggering a full decode, while preserving Pillow's
decompression-bomb handling and user-readable corrupt-image errors.

No Tk root and no network: the viewer is built via __new__ so __init__ (which
constructs Tk widgets) never runs.
"""
from __future__ import annotations

import io

import pytest

from PIL import Image, UnidentifiedImageError

from gui.components import image_viewer_window
from gui.components.image_viewer_window import ImageViewerWindow


def _viewer(max_pixels: int) -> ImageViewerWindow:
    viewer = ImageViewerWindow.__new__(ImageViewerWindow)
    viewer.max_pixels = max_pixels
    return viewer


class _StubImage:
    """Minimal stand-in for a lazily-opened Pillow image.

    Records load() invocations so tests can assert a full decode never happens
    after a rejection.
    """

    def __init__(self, size, load_calls):
        self.size = size
        self._load_calls = load_calls

    def load(self):
        self._load_calls.append(True)
        return self


def _patch_stub_open(monkeypatch, size):
    """Patch Image.open to return a stub of the given size; return load_calls."""
    load_calls: list = []

    def fake_open(_fp):
        return _StubImage(size, load_calls)

    monkeypatch.setattr(image_viewer_window.Image, "open", fake_open)
    return load_calls


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


# --- Rejection tests: load() must never be called ------------------------------


def test_over_limit_rejected_before_decode(monkeypatch):
    load_calls = _patch_stub_open(monkeypatch, (5000, 5000))  # 25M > 20M
    viewer = _viewer(20_000_000)
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


def test_zero_dimension_rejected(monkeypatch):
    load_calls = _patch_stub_open(monkeypatch, (0, 5))
    viewer = _viewer(20_000_000)
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


def test_negative_dimension_rejected(monkeypatch):
    load_calls = _patch_stub_open(monkeypatch, (-1, 5))
    viewer = _viewer(20_000_000)
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


def test_non_integer_dimension_rejected(monkeypatch):
    load_calls = _patch_stub_open(monkeypatch, ("x", 5))
    viewer = _viewer(20_000_000)
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


def test_boolean_dimension_rejected(monkeypatch):
    load_calls = _patch_stub_open(monkeypatch, (True, 5))
    viewer = _viewer(20_000_000)
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


@pytest.mark.parametrize("size", [(10,), (10, 20, 30)])
def test_wrong_arity_size_rejected(monkeypatch, size):
    load_calls = _patch_stub_open(monkeypatch, size)
    viewer = _viewer(20_000_000)
    with pytest.raises(ValueError):  # `w, h = img.size` unpack fails closed
        viewer._load_image_safe(b"ignored")
    assert load_calls == []


# --- Boundary / decode tests ---------------------------------------------------


def test_at_limit_calls_load_before_return(monkeypatch):
    flag = {"loaded": False}
    real_open = image_viewer_window.Image.open

    def spy_open(fp):
        img = real_open(fp)
        orig_load = img.load

        def load_spy(*args, **kwargs):
            flag["loaded"] = True
            return orig_load(*args, **kwargs)

        img.load = load_spy
        return img

    monkeypatch.setattr(image_viewer_window.Image, "open", spy_open)
    viewer = _viewer(16)  # 4 * 4 == 16, exactly at the limit
    result = viewer._load_image_safe(_png_bytes(4, 4))
    assert isinstance(result, Image.Image)
    assert flag["loaded"] is True  # load() ran inside the method, not deferred


def test_one_over_limit_rejected(monkeypatch):
    flag = {"loaded": False}
    real_open = image_viewer_window.Image.open

    def spy_open(fp):
        img = real_open(fp)
        orig_load = img.load

        def load_spy(*args, **kwargs):
            flag["loaded"] = True
            return orig_load(*args, **kwargs)

        img.load = load_spy
        return img

    monkeypatch.setattr(image_viewer_window.Image, "open", spy_open)
    viewer = _viewer(15)  # 4 * 4 == 16 > 15
    with pytest.raises(RuntimeError):
        viewer._load_image_safe(_png_bytes(4, 4))
    assert flag["loaded"] is False


# --- Corrupt + upstream-guard tests --------------------------------------------


def test_corrupt_image_surfaces_error():
    viewer = _viewer(20_000_000)
    with pytest.raises(UnidentifiedImageError):
        viewer._load_image_safe(b"not an image")


def test_decompression_bomb_error_propagates(monkeypatch):
    monkeypatch.setattr(image_viewer_window.Image, "MAX_IMAGE_PIXELS", 10)
    viewer = _viewer(20_000_000)  # project check would NOT reject this
    with pytest.raises(image_viewer_window.Image.DecompressionBombError):
        viewer._load_image_safe(_png_bytes(10, 10))  # 100 px > 2 * 10
