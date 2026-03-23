"""Unit tests for ScanManager config-path propagation."""

from pathlib import Path

from gui.utils import scan_manager as sm_mod
from gui.utils import scan_manager_protocol_mixin as mixin_mod


class _DummyBackendInterface:
    """Minimal stand-in for BackendInterface constructor side effects."""

    def __init__(self, backend_path: str):
        self.backend_path = Path(backend_path).resolve()
        self.config_path = self.backend_path / "conf" / "config.json"


class _DummyThread:
    """Non-running thread shim to avoid starting worker logic in unit tests."""

    def __init__(self, target=None, args=(), daemon=True):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        return None


def _make_scan_manager(tmp_path: Path):
    sm = sm_mod.ScanManager(gui_directory=str(tmp_path))
    sm.create_lock_file = lambda *a, **k: True
    sm.is_scan_active = lambda: False
    return sm


def test_start_scan_applies_explicit_config_path(monkeypatch, tmp_path):
    sm = _make_scan_manager(tmp_path)
    monkeypatch.setattr(sm_mod, "BackendInterface", _DummyBackendInterface)
    monkeypatch.setattr(sm_mod.threading, "Thread", _DummyThread)

    cfg = tmp_path / "conf" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    started = sm.start_scan(
        scan_options={"country": None},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
        config_path=str(cfg),
    )

    assert started is True
    assert sm.backend_interface.config_path == cfg.resolve()


def test_start_ftp_scan_applies_explicit_config_path(monkeypatch, tmp_path):
    sm = _make_scan_manager(tmp_path)
    # start_ftp_scan lives in the mixin module; patch there so monkeypatch intercepts.
    monkeypatch.setattr(mixin_mod, "BackendInterface", _DummyBackendInterface)
    monkeypatch.setattr(mixin_mod.threading, "Thread", _DummyThread)

    cfg = tmp_path / "conf" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    started = sm.start_ftp_scan(
        scan_options={"country": None},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
        config_path=str(cfg),
    )

    assert started is True
    assert sm.backend_interface.config_path == cfg.resolve()


def test_start_http_scan_applies_explicit_config_path(monkeypatch, tmp_path):
    sm = _make_scan_manager(tmp_path)
    # start_http_scan lives in the mixin module; patch there so monkeypatch intercepts.
    monkeypatch.setattr(mixin_mod, "BackendInterface", _DummyBackendInterface)
    monkeypatch.setattr(mixin_mod.threading, "Thread", _DummyThread)

    cfg = tmp_path / "conf" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{}", encoding="utf-8")

    started = sm.start_http_scan(
        scan_options={"country": None},
        backend_path=str(tmp_path),
        progress_callback=lambda *_: None,
        config_path=str(cfg),
    )

    assert started is True
    assert sm.backend_interface.config_path == cfg.resolve()
