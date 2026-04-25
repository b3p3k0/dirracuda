"""Reusable headless harness for server-ops scenario and fuzz tests."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from types import SimpleNamespace
import threading
import time
import tkinter as tk
from typing import Any, Dict, List, Optional

import gui.main as main_module
from gui.components.server_list_window.actions.batch_operations import (
    ServerListWindowBatchOperationsMixin,
)
from gui.components.server_list_window.actions.batch_status import (
    ServerListWindowBatchStatusMixin,
)
from gui.dashboard.widget import DashboardWidget
from gui.utils.running_tasks import get_running_task_registry


class FakeButton:
    """Tk-like button double with configurable text/state tracking."""

    def __init__(self) -> None:
        self._state = tk.DISABLED
        self._text = "Running Tasks (0)"

    def winfo_exists(self) -> bool:
        return True

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self._state = kwargs["state"]
        if "text" in kwargs:
            self._text = kwargs["text"]


class FakeRoot:
    """Minimal root double for close-behavior tests."""

    def __init__(self) -> None:
        self.destroy_calls = 0

    def winfo_exists(self) -> bool:
        return True

    def update_idletasks(self):
        return None

    def update(self):
        return None

    def destroy(self) -> None:
        self.destroy_calls += 1


class FakeDialog:
    """Monitor dialog double with hide/show/finish hooks."""

    def __init__(self) -> None:
        self.hidden = False
        self.destroy_calls = 0
        self.show_calls = 0
        self.finished_calls: List[tuple[str, str]] = []

    def winfo_exists(self) -> bool:
        return True

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.hidden = False
        self.show_calls += 1

    def mark_finished(self, status: str, notes: str) -> None:
        self.finished_calls.append((status, notes))

    def destroy(self) -> None:
        self.destroy_calls += 1


class AsyncFakeDialog(FakeDialog):
    """Dialog double with async-style `after` scheduling for modal flow tests."""

    created: List["AsyncFakeDialog"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        super().__init__()
        self.exists = True
        self.protocol_handlers: Dict[str, Any] = {}
        self.after_callbacks: List[str] = []
        self.hidden = False
        self._timers: List[threading.Timer] = []
        self.grab_release_calls = 0
        AsyncFakeDialog.created.append(self)

    def title(self, *_args, **_kwargs) -> None:
        return None

    def geometry(self, *_args, **_kwargs) -> None:
        return None

    def transient(self, *_args, **_kwargs) -> None:
        return None

    def grab_set(self) -> None:
        return None

    def grab_release(self) -> None:
        self.grab_release_calls += 1

    def protocol(self, name: str, callback) -> None:
        self.protocol_handlers[name] = callback

    def update_idletasks(self) -> None:
        return None

    def after(self, ms: int, callback, *args):
        self.after_callbacks.append(getattr(callback, "__name__", repr(callback)))
        delay = max(0.0, float(ms) / 1000.0)
        timer = threading.Timer(delay, callback, args=args)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)
        return len(self.after_callbacks)

    def winfo_exists(self) -> bool:
        return bool(self.exists)

    def withdraw(self) -> None:
        self.hidden = True

    def deiconify(self) -> None:
        self.hidden = False
        self.show_calls += 1

    def lift(self) -> None:
        return None

    def focus_force(self) -> None:
        return None

    def destroy(self) -> None:
        self.exists = False
        super().destroy()

    def trigger_close(self) -> None:
        callback = self.protocol_handlers.get("WM_DELETE_WINDOW")
        if callback:
            callback()


class WrappedDialogWindow:
    def winfo_exists(self) -> bool:
        return True


class WrappedDialog:
    """Dialog wrapper variant that exposes winfo_exists on .window."""

    def __init__(self) -> None:
        self.window = WrappedDialogWindow()
        self.show_calls = 0

    def show(self) -> None:
        self.show_calls += 1

    def mark_finished(self, _status: str, _notes: str) -> None:
        return None

    def destroy(self) -> None:
        return None


class FakeExecutor:
    """Executor double for active job records."""

    def __init__(self) -> None:
        self.shutdown_calls: List[tuple[bool, bool]] = []

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class FakeTheme:
    """Theme double for dashboard producer tests."""

    def apply_to_widget(self, *_args, **_kwargs):
        return None

    def apply_theme_to_application(self, *_args, **_kwargs):
        return None


class FakeSettingsManager:
    """Settings manager double with dict-backed values."""

    def __init__(self, values: Optional[Dict[str, Any]] = None):
        self._values = dict(values or {})

    def get_setting(self, key: str, default=None):
        return self._values.get(key, default)


class FakeModalParent:
    """Parent double whose wait_window blocks until dialog destruction."""

    def wait_window(self, dialog) -> None:
        deadline = time.time() + 3.0
        while getattr(dialog, "winfo_exists", lambda: False)() and time.time() < deadline:
            time.sleep(0.01)

    def after(self, _ms: int, _callback, *_args):
        return None


class FakeWidget:
    """Tk-like widget double for labels/buttons."""

    def __init__(self, *_args, **kwargs):
        self.command = kwargs.get("command")
        self.text = kwargs.get("text", "")

    def pack(self, *_args, **_kwargs):
        return None

    def config(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]

    configure = config


class FakeProgressbar(FakeWidget):
    """Progressbar double with dict-style value assignment."""

    def __init__(self, *_args, **kwargs):
        super().__init__(*_args, **kwargs)
        self.value = 0

    def __setitem__(self, key, value):
        setattr(self, key, value)


class FakeSeDorkTree:
    """Treeview-like selection double for SE dork probe tests."""

    def __init__(self, selected_ids: List[str]):
        self._selected = list(selected_ids)
        self._selection_set_calls: List[tuple[str, ...]] = []

    def selection(self):
        return list(self._selected)

    def exists(self, iid: str) -> bool:
        return iid in self._selected

    def selection_set(self, *iids):
        self._selected = list(iids)
        self._selection_set_calls.append(tuple(iids))


class FakeSeDorkDialog:
    """BatchStatusDialog-like double for SE dork probe monitor tests."""

    created: List["FakeSeDorkDialog"] = []

    def __init__(self, parent, theme, *, title, fields, on_cancel, total=None):
        self.parent = parent
        self.theme = theme
        self.title = title
        self.fields = fields
        self.on_cancel = on_cancel
        self.total = total
        self.window = SimpleNamespace(
            winfo_exists=lambda: True,
            update_idletasks=lambda: None,
            update=lambda: None,
        )
        self.progress_calls: List[tuple[int, int, Optional[str]]] = []
        self.finished_calls: List[tuple[str, str]] = []
        self.show_calls = 0
        FakeSeDorkDialog.created.append(self)

    def update_progress(self, done: int, total: int, message: Optional[str] = None) -> None:
        self.progress_calls.append((done, total, message))

    def mark_finished(self, status: str, notes: str) -> None:
        self.finished_calls.append((status, notes))

    def show(self) -> None:
        self.show_calls += 1


class InlineExecutor:
    """Deterministic executor that executes submitted work immediately."""

    def __init__(self, max_workers=None, thread_name_prefix=None):
        self.max_workers = max_workers
        self.thread_name_prefix = thread_name_prefix
        self.shutdown_calls: List[tuple[bool, bool]] = []

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as exc:  # pragma: no cover - test helper safety
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


class FakeSeDorkConnection:
    """DB connection double for SE dork probe tests."""

    def __init__(self) -> None:
        self.commit_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class BatchStatusHarness(ServerListWindowBatchStatusMixin):
    """Concrete harness implementing required mixin collaborators."""

    def __init__(self) -> None:
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.batch_status_dialog = None
        self.window = SimpleNamespace(winfo_exists=lambda: True)
        self._running_tasks_subscribed = False
        self.running_tasks_button = None
        self.running_tasks_window = None

    def _update_action_buttons_state(self):
        return None

    def _set_status(self, _message: str):
        return None

    def _flush_pending_refresh(self):
        return None

    def _set_table_interaction_enabled(self, _enabled: bool):
        return None

    def _show_batch_summary(self, _job_type: str, _results: List[Dict[str, Any]]):
        return None

    def _maybe_show_clamav_dialog(self, _results, _clamav_cfg, **_kwargs):
        return None

    def _update_stop_button_style(self, _batch_active: bool):
        return None


class PryOperationsHarness(ServerListWindowBatchOperationsMixin):
    """Harness focused on pry-selection routing behavior."""

    def __init__(self, selected_targets: List[Dict[str, Any]]) -> None:
        self.window = SimpleNamespace()
        self._pry_unlocked = True
        self._selected_targets = list(selected_targets)
        self._started_jobs: List[tuple[str, List[Dict[str, Any]], Dict[str, Any]]] = []
        self.settings_manager = None
        self.db_reader = SimpleNamespace(
            get_denied_shares=lambda _ip, limit=100: [],
            get_accessible_shares=lambda _ip: [],
        )
        self.theme = None

    def _hide_context_menu(self) -> None:
        return None

    def _build_selected_targets(self) -> List[Dict[str, Any]]:
        return list(self._selected_targets)

    def _start_batch_job(self, job_type: str, targets: List[Dict[str, Any]], options: Dict[str, Any]) -> None:
        self._started_jobs.append((job_type, list(targets), dict(options)))


@dataclass
class JobRecord:
    """Input model for creating deterministic active-job entries."""

    job_id: str
    job_type: str
    total: int
    unit_label: str = "targets"
    ip_address: str = "203.0.113.10"


def create_active_job(
    harness: BatchStatusHarness,
    record: JobRecord,
    *,
    dialog: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create one active-job record and return it."""

    monitor = dialog if dialog is not None else FakeDialog()
    job = {
        "id": record.job_id,
        "type": record.job_type,
        "targets": [{"ip_address": record.ip_address}],
        "options": {},
        "executor": FakeExecutor(),
        "cancel_event": threading.Event(),
        "results": [],
        "completed": 0,
        "total": int(record.total),
        "unit_label": record.unit_label,
        "futures": [],
        "dialog": monitor,
    }
    harness.active_jobs[record.job_id] = job
    return job


def make_dashboard_tasks_stub() -> DashboardWidget:
    """Create DashboardWidget instance without full Tk initialization."""

    dash = DashboardWidget.__new__(DashboardWidget)
    dash.running_tasks_button = FakeButton()
    return dash


def make_dashboard_scan_task_stub() -> tuple[DashboardWidget, Dict[str, int]]:
    """Create dashboard stub for scan-task lifecycle tests."""

    counters = {
        "reopen_calls": 0,
        "interrupt_calls": 0,
        "clear_queue_calls": 0,
    }
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.running_tasks_registry = get_running_task_registry()
    dash.running_tasks_button = FakeButton()
    dash._scan_task_id = None
    dash._queued_scan_total = 0
    dash._queued_scan_protocols = []
    dash._queued_scan_active = False
    dash.scan_manager = SimpleNamespace(
        is_scanning=False,
        interrupt_scan=lambda: counters.__setitem__("interrupt_calls", counters["interrupt_calls"] + 1),
    )
    dash._clear_queued_scan_state = lambda: (
        counters.__setitem__("clear_queue_calls", counters["clear_queue_calls"] + 1),
        setattr(dash, "_queued_scan_active", False),
        setattr(dash, "_queued_scan_protocols", []),
    )
    dash._reopen_scan_output_dialog = lambda: counters.__setitem__("reopen_calls", counters["reopen_calls"] + 1)
    return dash, counters


def make_dashboard_batch_task_stub(
    *,
    probe_release_event: Optional[threading.Event] = None,
    extract_release_event: Optional[threading.Event] = None,
) -> tuple[DashboardWidget, Dict[str, Any]]:
    """
    Create dashboard stub for post-scan probe/extract monitor lifecycle tests.

    release_event controls when worker stubs stop blocking.
    """

    state: Dict[str, Any] = {
        "probe_cancel_event": None,
        "extract_cancel_event": None,
    }
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = FakeModalParent()
    dash.theme = FakeTheme()
    dash.settings_manager = FakeSettingsManager(
        {
            "probe.batch_max_workers": 1,
            "probe.max_directories_per_share": 2,
            "probe.max_files_per_directory": 5,
            "probe.share_timeout_seconds": 3,
            "extract.batch_max_workers": 1,
            "extract.max_file_size_mb": 50,
            "extract.max_total_size_mb": 200,
            "extract.max_time_seconds": 300,
            "extract.max_files_per_target": 10,
            "extract.extension_mode": "allow_only",
        }
    )
    dash.current_scan_options = {}
    dash.running_tasks_registry = get_running_task_registry()
    dash._bulk_probe_progress_dialog = None
    dash._protocol_label_from_host_type = lambda host_type: {"S": "SMB", "F": "FTP", "H": "HTTP"}.get(
        str(host_type or "S").upper(),
        "Unknown",
    )

    def _probe_single_server(
        server,
        _max_dirs,
        _max_files,
        _timeout_seconds,
        _enable_rce,
        cancel_event,
    ):
        state["probe_cancel_event"] = cancel_event
        while (
            probe_release_event is not None
            and not probe_release_event.is_set()
            and not cancel_event.is_set()
        ):
            time.sleep(0.01)
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "protocol": "SMB",
                "action": "probe",
                "status": "cancelled",
                "notes": "Cancelled",
            }
        return {
            "ip_address": server.get("ip_address"),
            "protocol": "SMB",
            "action": "probe",
            "status": "success",
            "notes": "ok",
        }

    def _extract_single_server(
        server,
        _max_file_mb,
        _max_total_mb,
        _max_time,
        _max_files,
        _extension_mode,
        _included_extensions,
        _excluded_extensions,
        _quarantine_base_path,
        cancel_event,
        _clamav_cfg,
    ):
        state["extract_cancel_event"] = cancel_event
        while (
            extract_release_event is not None
            and not extract_release_event.is_set()
            and not cancel_event.is_set()
        ):
            time.sleep(0.01)
        if cancel_event.is_set():
            return {
                "ip_address": server.get("ip_address"),
                "protocol": "SMB",
                "action": "extract",
                "status": "cancelled",
                "notes": "Cancelled",
            }
        return {
            "ip_address": server.get("ip_address"),
            "protocol": "SMB",
            "action": "extract",
            "status": "success",
            "notes": "ok",
        }

    dash._probe_single_server = _probe_single_server
    dash._extract_single_server = _extract_single_server
    return dash, state


def patch_dashboard_batch_dialog_stack(monkeypatch) -> None:
    """Patch dashboard module Tk constructors for headless batch monitor tests."""

    AsyncFakeDialog.created.clear()
    monkeypatch.setattr("gui.components.dashboard.tk.Toplevel", AsyncFakeDialog)
    monkeypatch.setattr("gui.components.dashboard.tk.Label", FakeWidget)
    monkeypatch.setattr("gui.components.dashboard.tk.Button", FakeWidget)
    monkeypatch.setattr("gui.components.dashboard.ttk.Progressbar", FakeProgressbar)


def patch_main_shutdown_helpers(monkeypatch) -> None:
    """Neutralize tmpfs shutdown branches for close-behavior tests."""

    monkeypatch.setattr(main_module, "get_tmpfs_runtime_state", lambda: {"tmpfs_active": False})
    monkeypatch.setattr(main_module, "tmpfs_has_quarantined_files", lambda: False)
    monkeypatch.setattr(main_module, "cleanup_tmpfs_quarantine", lambda: {"ok": True, "message": ""})


def make_main_app_stub() -> main_module.SMBSeekGUI:
    """Construct a minimal SMBSeekGUI object for _on_closing tests."""

    app = main_module.SMBSeekGUI.__new__(main_module.SMBSeekGUI)
    app.root = FakeRoot()
    app._pending_tmpfs_startup_warning = None
    app.drill_down_windows = {}
    app.db_reader = SimpleNamespace(clear_cache=lambda: None)
    app.ui_dispatcher = None
    app.scan_manager = SimpleNamespace(is_scanning=False, interrupt_scan=lambda: True)
    app.settings_manager = None
    app.backend_interface = None
    app.mock_mode = False
    return app


def force_future_result(payload: Dict[str, Any]) -> Future:
    """Create a completed Future with a known payload."""

    fut = Future()
    fut.set_result(payload)
    return fut


def make_se_dork_probe_window_stub(window_cls, rows: Dict[str, Dict[str, Any]]):
    """Create SeDorkBrowserWindow-like stub object without Tk construction."""

    window = window_cls.__new__(window_cls)
    window.parent = SimpleNamespace()
    window.db_path = None
    window.theme = None
    window._settings_manager = SimpleNamespace(
        get_setting=lambda _k, default=None: default,
        get_smbseek_config_path=lambda: None,
    )
    window._add_record_callback = None
    window._row_by_iid = dict(rows)
    window._context_menu_visible = False
    window.window = SimpleNamespace()
    window.tree = FakeSeDorkTree(list(rows.keys()))
    window._status_label = SimpleNamespace(configure=lambda **_kwargs: None)
    window._context_menu = SimpleNamespace(unpost=lambda: None)
    return window
