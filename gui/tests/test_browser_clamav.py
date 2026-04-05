"""Targeted tests for browser-download ClamAV integration."""

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.components.unified_browser_window import (  # noqa: E402
    FtpBrowserWindow,
    HttpBrowserWindow,
    SmbBrowserWindow,
    UnifiedBrowserCore,
    _load_ftp_browser_config,
    _load_http_browser_config,
    _load_smb_browser_config,
)
from gui.utils import session_flags  # noqa: E402
from gui.utils.extract_runner import (  # noqa: E402
    build_browser_download_clamav_setup,
)
from shared.quarantine_postprocess import PostProcessResult  # noqa: E402


class _Var:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self):
        return None


def _immediate_after(_delay, callback, *args):
    callback(*args)


def _make_core_stub() -> UnifiedBrowserCore:
    c = UnifiedBrowserCore.__new__(UnifiedBrowserCore)
    c.busy = True
    c.window = MagicMock()
    c.window.after = _immediate_after
    c.btn_cancel = MagicMock()
    c._set_buttons_busy = MagicMock()
    c._set_status = MagicMock()
    c._window_alive = MagicMock(return_value=True)
    c._maybe_show_clamav_dialog = MagicMock(return_value=False)
    c.config = {"clamav": {"enabled": True, "show_results": True}}
    c.theme = None
    c.ip_address = "1.2.3.4"
    return c


def _make_ftp_window(tmp_path: Path) -> FtpBrowserWindow:
    win = FtpBrowserWindow.__new__(FtpBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.port = 21
    win.window = MagicMock()
    win.window.after = _immediate_after
    win.theme = None
    base_config = _load_ftp_browser_config(None)
    base_config["quarantine_base"] = str(tmp_path / "q")
    base_config["clamav"] = {"enabled": True}
    win.config = base_config
    win._cancel_event = threading.Event()
    win._set_status = MagicMock()
    win._on_download_done = MagicMock()
    win._navigator = MagicMock()
    win.download_workers = 1
    win.download_large_mb = 25
    win.workers_var = _Var(1)
    win.large_mb_var = _Var(25)
    return win


def _make_http_window(tmp_path: Path) -> HttpBrowserWindow:
    win = HttpBrowserWindow.__new__(HttpBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.window = MagicMock()
    win.window.after = _immediate_after
    win.theme = None
    base_config = _load_http_browser_config(None)
    base_config["quarantine_base"] = str(tmp_path / "q")
    base_config["clamav"] = {"enabled": True}
    win.config = base_config
    win._cancel_event = threading.Event()
    win._set_status = MagicMock()
    win._on_download_done = MagicMock()
    win._navigator = MagicMock()
    win.download_workers = 1
    win.download_large_mb = 25
    win.workers_var = _Var(1)
    win.large_mb_var = _Var(25)
    return win


def _make_smb_window(tmp_path: Path) -> SmbBrowserWindow:
    win = SmbBrowserWindow.__new__(SmbBrowserWindow)
    win.ip_address = "10.20.30.40"
    win.current_share = "pub"
    win.username = ""
    win.password = ""
    win.window = MagicMock()
    win.theme = None
    win.config = {
        "allow_smb1": True,
        "connect_timeout_seconds": 1,
        "request_timeout_seconds": 1,
        "max_entries_per_dir": 10,
        "max_depth": 1,
        "max_path_length": 240,
        "download_chunk_mb": 1,
        "quarantine_root": str(tmp_path / "q"),
        "clamav": {"enabled": True},
    }
    win.workers_var = _Var(1)
    win.large_mb_var = _Var(1)
    win.download_workers = 1
    win.download_large_mb = 1
    win.download_cancel_event = None
    win._ensure_connected = MagicMock()
    win._set_busy = MagicMock()
    win._set_status = MagicMock()
    win._safe_after = lambda _d, fn, *a: fn(*a)
    win._window_alive = MagicMock(return_value=True)
    win._handle_extracted_success = MagicMock()
    win._map_download_error = lambda e: str(e)
    win._on_smb_download_done = MagicMock()
    win._maybe_show_clamav_dialog = MagicMock(return_value=False)
    win.navigator = MagicMock()
    return win


class TestConfigLoaders:
    def test_ftp_config_loader_reads_clamav_section(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"ftp_browser": {"max_entries": 123}, "clamav": {"enabled": True}}), encoding="utf-8")
        loaded = _load_ftp_browser_config(str(cfg))
        assert loaded["max_entries"] == 123
        assert loaded["clamav"]["enabled"] is True

    def test_ftp_config_loader_empty_clamav_when_absent(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"ftp_browser": {"max_entries": 111}}), encoding="utf-8")
        loaded = _load_ftp_browser_config(str(cfg))
        assert loaded["clamav"] == {}

    def test_http_config_loader_reads_clamav_section(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"http_browser": {"max_entries": 234}, "clamav": {"enabled": True}}), encoding="utf-8")
        loaded = _load_http_browser_config(str(cfg))
        assert loaded["max_entries"] == 234
        assert loaded["clamav"]["enabled"] is True

    def test_smb_config_loader_reads_clamav_section(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"file_browser": {"max_depth": 3}, "clamav": {"enabled": True}}), encoding="utf-8")
        loaded = _load_smb_browser_config(str(cfg))
        assert loaded["max_depth"] == 3
        assert loaded["clamav"]["enabled"] is True


class TestBrowserSetupHelpers:
    def test_build_browser_setup_disabled(self, tmp_path):
        pp, accum, err = build_browser_download_clamav_setup({}, "1.2.3.4", tmp_path / "q" / "ftp_root", "ftp_root")
        assert pp is None
        assert accum is None
        assert err is None

    def test_build_browser_setup_enabled(self, tmp_path):
        pp, accum, err = build_browser_download_clamav_setup({"enabled": True}, "1.2.3.4", tmp_path / "q" / "ftp_root", "ftp_root")
        assert callable(pp)
        assert isinstance(accum, dict)
        assert accum["enabled"] is True
        assert err is None

    def test_build_browser_setup_init_error_returns_error_msg(self, tmp_path):
        with patch("gui.utils.extract_runner._build_promotion_config", side_effect=RuntimeError("boom")):
            pp, accum, err = build_browser_download_clamav_setup(
                {"enabled": True}, "1.2.3.4", tmp_path / "q" / "ftp_root", "ftp_root"
            )
        assert pp is None
        assert accum is None
        assert isinstance(err, str)
        assert "ClamAV init failed" in err


class TestCoreDialogBehavior:
    def test_on_download_done_clamav_shown_no_plain_messagebox(self):
        core = _make_core_stub()
        core._maybe_show_clamav_dialog.return_value = True
        with patch("gui.components.unified_browser_window.messagebox.showinfo") as m:
            UnifiedBrowserCore._on_download_done(core, 1, 1, "/tmp/q", {"enabled": True, "promoted": 1, "infected": 0})
        assert m.call_count == 0

    def test_on_download_done_clamav_not_shown_destination_aware_fallback(self):
        core = _make_core_stub()
        core._maybe_show_clamav_dialog.return_value = False
        with patch("gui.components.unified_browser_window.messagebox.showinfo") as m:
            UnifiedBrowserCore._on_download_done(core, 1, 1, "/tmp/q", {"enabled": True, "promoted": 1, "infected": 0})
        assert m.call_count == 1
        args, _kwargs = m.call_args
        assert "clean -> extracted" in args[1]

    def test_on_download_done_disabled_plain_messagebox(self):
        core = _make_core_stub()
        with patch("gui.components.unified_browser_window.messagebox.showinfo") as m:
            UnifiedBrowserCore._on_download_done(core, 1, 1, "/tmp/q", None)
        assert m.call_count == 1
        args, _kwargs = m.call_args
        assert "to quarantine" in args[1]

    def test_maybe_show_dialog_muted_returns_false(self):
        core = _make_core_stub()
        session_flags.set_flag(session_flags.CLAMAV_MUTE_KEY, True)
        try:
            with patch("gui.components.unified_browser_window.show_clamav_results_dialog", create=True):
                shown = UnifiedBrowserCore._maybe_show_clamav_dialog(core, {"enabled": True, "files_scanned": 1})
            assert shown is False
        finally:
            session_flags.clear_flag(session_flags.CLAMAV_MUTE_KEY)

    def test_maybe_show_dialog_zero_files_scanned_returns_false(self):
        core = _make_core_stub()
        shown = UnifiedBrowserCore._maybe_show_clamav_dialog(core, {"enabled": True, "files_scanned": 0})
        assert shown is False

    def test_maybe_show_dialog_includes_ip_address(self):
        core = _make_core_stub()

        def _should_show(job_type, results, _cfg):
            assert job_type == "extract"
            assert results[0]["ip_address"] == "1.2.3.4"
            return True

        with patch("gui.components.clamav_results_dialog.should_show_clamav_dialog", side_effect=_should_show), patch(
            "gui.components.clamav_results_dialog.show_clamav_results_dialog"
        ):
            shown = UnifiedBrowserCore._maybe_show_clamav_dialog(core, {"enabled": True, "files_scanned": 1})
        assert shown is True

    def test_on_smb_download_done_clamav_shown_no_messagebox(self):
        core = _make_core_stub()
        core._maybe_show_clamav_dialog.return_value = True
        with patch("gui.components.unified_browser_window.messagebox.showinfo") as m:
            UnifiedBrowserCore._on_smb_download_done(core, "ok", {"enabled": True, "files_scanned": 1})
        assert m.call_count == 0

    def test_on_smb_download_done_clamav_not_shown_messagebox(self):
        core = _make_core_stub()
        core._maybe_show_clamav_dialog.return_value = False
        with patch("gui.components.unified_browser_window.messagebox.showinfo") as m:
            UnifiedBrowserCore._on_smb_download_done(core, "ok", {"enabled": True, "files_scanned": 1})
        assert m.call_count == 1


class TestFtpHttpDownloadWiring:
    def test_ftp_download_thread_init_error_surfaces_to_status(self, tmp_path):
        win = _make_ftp_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "ftp_root"
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(None, None, "ClamAV init failed: boom"),
        ):
            win._download_thread_fn([])
        win._set_status.assert_any_call("ClamAV init failed: boom")

    def test_http_download_thread_init_error_surfaces_to_status(self, tmp_path):
        win = _make_http_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "http_root"
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(None, None, "ClamAV init failed: boom"),
        ):
            win._download_thread_fn([])
        win._set_status.assert_any_call("ClamAV init failed: boom")

    def test_ftp_download_thread_calls_postprocessor(self, tmp_path):
        win = _make_ftp_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "ftp_root"
        saved = qdir / "a.txt"
        accum = {"enabled": True, "errors": 0, "error_items": []}

        def _pp(_inp):
            return PostProcessResult(
                final_path=saved,
                verdict="clean",
                moved=True,
                destination="extracted",
                metadata=None,
                error=None,
            )

        mock_nav = MagicMock()
        mock_nav.download_file.return_value = SimpleNamespace(saved_path=saved)
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(_pp, accum, None)), \
             patch("gui.utils.extract_runner.update_browser_clamav_accum") as upd, \
             patch("shared.ftp_browser.FtpNavigator", return_value=mock_nav), \
             patch("gui.components.unified_browser_window.threading.Thread", _ImmediateThread):
            win._download_thread_fn([("/a.txt", 10)])

        assert upd.call_count == 1
        assert win._on_download_done.call_count == 1
        args = win._on_download_done.call_args.args
        assert args[3] is accum

    def test_ftp_download_thread_disabled_no_accum(self, tmp_path):
        win = _make_ftp_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "ftp_root"
        saved = qdir / "a.txt"
        mock_nav = MagicMock()
        mock_nav.download_file.return_value = SimpleNamespace(saved_path=saved)
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(None, None, None)), \
             patch("shared.ftp_browser.FtpNavigator", return_value=mock_nav), \
             patch("gui.components.unified_browser_window.threading.Thread", _ImmediateThread):
            win._download_thread_fn([("/a.txt", 10)])

        args = win._on_download_done.call_args.args
        assert args[3] is None

    def test_ftp_download_pp_exception_logged_to_accum_and_quarantine(self, tmp_path):
        win = _make_ftp_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "ftp_root"
        saved = qdir / "a.txt"
        accum = {"enabled": True, "errors": 0, "error_items": []}

        def _pp(_inp):
            raise RuntimeError("pp boom")

        mock_nav = MagicMock()
        mock_nav.download_file.return_value = SimpleNamespace(saved_path=saved)
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(_pp, accum, None)), \
             patch("shared.quarantine.log_quarantine_event") as log_evt, \
             patch("shared.ftp_browser.FtpNavigator", return_value=mock_nav), \
             patch("gui.components.unified_browser_window.threading.Thread", _ImmediateThread):
            win._download_thread_fn([("/a.txt", 10)])

        assert accum["errors"] == 1
        assert "pp boom" in accum["error_items"][0]["error"]
        assert any("clamav post-process error" in str(c.args[1]) for c in log_evt.call_args_list)

    def test_http_download_thread_calls_postprocessor(self, tmp_path):
        win = _make_http_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "http_root"
        saved = qdir / "a.txt"
        accum = {"enabled": True, "errors": 0, "error_items": []}

        def _pp(_inp):
            return PostProcessResult(
                final_path=saved,
                verdict="clean",
                moved=True,
                destination="extracted",
                metadata=None,
                error=None,
            )

        win._navigator.download_file.return_value = SimpleNamespace(saved_path=saved)
        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(_pp, accum, None),
        ), patch("gui.utils.extract_runner.update_browser_clamav_accum") as upd:
            win._download_thread_fn([("/a.txt", 10)])

        assert upd.call_count == 1


class TestSmbWorkerWiring:
    def _patch_smb_nav(self, monkeypatch, *, fail_on: set = None, save_root: Path = None):
        fail_on = fail_on or set()
        save_root = save_root or Path("/tmp")

        class _Nav:
            def __init__(self, **_kwargs):
                pass

            def connect(self, **_kwargs):
                return None

            def cancel(self):
                return None

            def disconnect(self):
                return None

            def download_file(self, remote_path, dest_dir, preserve_structure=True, mtime=None, progress_callback=None):
                if remote_path in fail_on:
                    raise RuntimeError("download failed")
                safe = remote_path.strip("/").replace("/", "_") or "file"
                return SimpleNamespace(saved_path=Path(dest_dir) / safe)

        monkeypatch.setattr("shared.smb_browser.SMBNavigator", _Nav)

    def test_smb_download_worker_init_error_surfaces_to_status(self, tmp_path, monkeypatch):
        win = _make_smb_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "pub"
        monkeypatch.setattr("gui.components.unified_browser_window.threading.Thread", _ImmediateThread)
        self._patch_smb_nav(monkeypatch)

        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(None, None, "ClamAV init failed: boom"),
        ):
            win._start_download_thread([("/a.txt", None, 10)], [], None)

        win._set_status.assert_any_call("ClamAV init failed: boom")

    def test_smb_consumer_postprocessor_called_accum_updated(self, tmp_path, monkeypatch):
        win = _make_smb_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "pub"
        monkeypatch.setattr("gui.components.unified_browser_window.threading.Thread", _ImmediateThread)
        self._patch_smb_nav(monkeypatch)
        accum = {
            "enabled": True,
            "files_scanned": 0,
            "clean": 0,
            "infected": 0,
            "errors": 0,
            "promoted": 0,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        }

        def _pp(_inp):
            return PostProcessResult(
                final_path=qdir / "a.txt",
                verdict="clean",
                moved=True,
                destination="extracted",
                metadata=None,
                error=None,
            )

        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(_pp, accum, None),
        ), patch("gui.utils.extract_runner.update_browser_clamav_accum") as upd:
            win._start_download_thread([("/a.txt", None, 10)], [], None)

        assert upd.call_count == 1
        assert win._on_smb_download_done.call_count == 1

    def test_smb_consumer_pp_exception_logged_to_accum_and_quarantine(self, tmp_path, monkeypatch):
        win = _make_smb_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "pub"
        monkeypatch.setattr("gui.components.unified_browser_window.threading.Thread", _ImmediateThread)
        self._patch_smb_nav(monkeypatch)
        accum = {
            "enabled": True,
            "files_scanned": 0,
            "clean": 0,
            "infected": 0,
            "errors": 0,
            "promoted": 0,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        }

        def _pp(_inp):
            raise RuntimeError("pp fail")

        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(_pp, accum, None),
        ), patch("shared.quarantine.log_quarantine_event") as log_evt:
            win._start_download_thread([("/a.txt", None, 10)], [], None)

        assert accum["errors"] == 1
        assert "pp fail" in accum["error_items"][0]["error"]
        assert any("clamav post-process error" in str(c.args[1]) for c in log_evt.call_args_list)

    def test_smb_mixed_outcome_shows_single_warning_with_av_totals(self, tmp_path, monkeypatch):
        win = _make_smb_window(tmp_path)
        qdir = tmp_path / "q" / "host" / "20260328" / "pub"
        monkeypatch.setattr("gui.components.unified_browser_window.threading.Thread", _ImmediateThread)
        self._patch_smb_nav(monkeypatch, fail_on={"/b.txt"})
        accum = {
            "enabled": True,
            "files_scanned": 1,
            "clean": 1,
            "infected": 0,
            "errors": 0,
            "promoted": 1,
            "known_bad_moved": 0,
            "infected_items": [],
            "error_items": [],
        }

        def _pp(_inp):
            return PostProcessResult(
                final_path=qdir / "a.txt",
                verdict="clean",
                moved=True,
                destination="extracted",
                metadata=None,
                error=None,
            )

        with patch("shared.quarantine.build_quarantine_path", return_value=qdir), patch(
            "gui.utils.extract_runner.build_browser_download_clamav_setup",
            return_value=(_pp, accum, None),
        ), patch("gui.components.unified_browser_window.messagebox.showwarning") as warn:
            win._start_download_thread([("/a.txt", None, 10), ("/b.txt", None, 10)], [], None)

        assert warn.call_count == 1
        msg = warn.call_args.args[1]
        assert "ClamAV totals" in msg
        assert win._on_smb_download_done.call_count == 0


# ---------------------------------------------------------------------------
# C2 runtime behavior tests
# ---------------------------------------------------------------------------

import queue as _queue_module  # noqa: E402 — used in _drain_paths


class _NoStartThread:
    """Captures threading.Thread instances without actually starting them."""
    _instances: list = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        _NoStartThread._instances.append(self)

    def start(self):
        pass

    def join(self):
        pass


def _drain_paths(q) -> set:
    """Drain a queue of (path, size) tuples and return the set of paths."""
    paths = set()
    while True:
        try:
            paths.add(q.get_nowait()[0])
        except _queue_module.Empty:
            break
    return paths


class TestC2RuntimeBehavior:
    def test_ftp_worker_count_starts_correct_thread_count(self, tmp_path):
        """download_workers=2 → 2 small workers + 1 large worker = 3 threads."""
        _NoStartThread._instances.clear()
        win = _make_ftp_window(tmp_path)
        win.download_workers = 2
        win.workers_var = _Var(2)
        with patch("shared.quarantine.build_quarantine_path", return_value=tmp_path / "q"), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(None, None, None)), \
             patch("shared.ftp_browser.FtpNavigator"), \
             patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
            win._download_thread_fn([])
        assert len(_NoStartThread._instances) == 3

    def test_ftp_large_file_threshold_routes_to_correct_queues(self, tmp_path):
        """Files above threshold go to q_large; others go to q_small."""
        _NoStartThread._instances.clear()
        win = _make_ftp_window(tmp_path)
        win.download_workers = 1
        win.workers_var = _Var(1)
        win.download_large_mb = 10
        win.large_mb_var = _Var(10)
        file_list = [("/large.bin", 15 * 1024 * 1024), ("/small.txt", 100)]
        with patch("shared.quarantine.build_quarantine_path", return_value=tmp_path / "q"), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(None, None, None)), \
             patch("shared.ftp_browser.FtpNavigator"), \
             patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
            win._download_thread_fn(file_list)

        # worker_count=1 → exactly 2 threads: 1 small + 1 large
        assert len(_NoStartThread._instances) == 2, (
            f"Expected 2 consumer threads, got {len(_NoStartThread._instances)}"
        )
        q_small = _NoStartThread._instances[0]._args[0]
        q_large = _NoStartThread._instances[-1]._args[0]
        assert q_small is not q_large

        assert _drain_paths(q_large) == {"/large.bin"}
        assert _drain_paths(q_small) == {"/small.txt"}

    def test_http_worker_count_starts_correct_thread_count(self, tmp_path):
        """download_workers=2 → 2 consumer threads (single queue)."""
        _NoStartThread._instances.clear()
        win = _make_http_window(tmp_path)
        win.download_workers = 2
        win.workers_var = _Var(2)
        with patch("shared.quarantine.build_quarantine_path", return_value=tmp_path / "q"), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(None, None, None)), \
             patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
            win._download_thread_fn([])
        assert len(_NoStartThread._instances) == 2

    def test_http_no_large_file_routing_all_files_in_one_queue(self, tmp_path):
        """HTTP uses a single queue regardless of file size; no large-file split."""
        _NoStartThread._instances.clear()
        win = _make_http_window(tmp_path)
        win.download_workers = 1
        win.workers_var = _Var(1)
        win.download_large_mb = 10
        win.large_mb_var = _Var(10)
        file_list = [("/large.bin", 15 * 1024 * 1024), ("/small.txt", 100)]

        captured_queues: list = []
        original_queue = _queue_module.Queue

        def _capturing_queue(*args, **kwargs):
            q = original_queue(*args, **kwargs)
            captured_queues.append(q)
            return q

        with patch("shared.quarantine.build_quarantine_path", return_value=tmp_path / "q"), \
             patch("gui.utils.extract_runner.build_browser_download_clamav_setup",
                   return_value=(None, None, None)), \
             patch("gui.components.unified_browser_window.queue.Queue",
                   side_effect=_capturing_queue), \
             patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
            win._download_thread_fn(file_list)

        # worker_count=1 → exactly 1 consumer thread
        assert len(_NoStartThread._instances) == 1
        # Exactly 1 queue created (no q_large; HTTP has no large-file routing)
        assert len(captured_queues) == 1
        assert _drain_paths(captured_queues[0]) == {"/large.bin", "/small.txt"}
