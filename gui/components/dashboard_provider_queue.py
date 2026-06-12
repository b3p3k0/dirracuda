"""Serial provider scheduling for unified desktop scans."""

from __future__ import annotations

from dataclasses import dataclass
import sys
import tkinter as tk
from typing import Any, Iterable, Optional

from gui.utils import safe_messagebox as _fallback_msgbox


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    priority: int
    launcher: str


PROVIDER_SPECS = {
    "reddit": ProviderSpec("reddit", "Reddit", 100, "start_reddit_scan"),
    "searxng": ProviderSpec("searxng", "SearXNG", 200, "start_searxng_scan"),
    "shodan": ProviderSpec("shodan", "Shodan", 300, "start_shodan_provider"),
}


def _mb():
    mod = sys.modules.get("gui.components.dashboard")
    if mod is not None and hasattr(mod, "messagebox"):
        return mod.messagebox
    return _fallback_msgbox


def _hook(dash, method_name: str, *args, **kwargs):
    method = getattr(dash, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args, **kwargs)
    except Exception:
        return None


def rank_providers(providers: Iterable[Any]) -> list[str]:
    """Return supported providers in priority order with stable tie-breaking."""
    selected: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for request_index, raw in enumerate(providers):
        key = str(raw or "").strip().lower()
        spec = PROVIDER_SPECS.get(key)
        if spec is None or key in seen:
            continue
        seen.add(key)
        selected.append((spec.priority, request_index, key))
    selected.sort()
    return [key for _priority, _index, key in selected]


def is_provider_queue_active(dash) -> bool:
    return bool(getattr(dash, "_provider_queue_active", False))


def is_current_provider(dash, provider: str) -> bool:
    return bool(
        is_provider_queue_active(dash)
        and getattr(dash, "_provider_queue_current", None) == provider
    )


def report_launch_error(
    dash,
    *,
    queue_managed: bool,
    title: str,
    message: str,
) -> None:
    if queue_managed:
        dash._provider_queue_launch_error = message
        _hook(dash, "_log_status_event", message)
    else:
        _mb().showerror(title, message)


def _update_task(dash, *, state: str, progress: str) -> None:
    task_id = getattr(dash, "_provider_queue_task_id", None)
    if task_id:
        _hook(
            dash,
            "_update_running_task",
            task_id,
            name="Unified Provider Queue",
            state=state,
            progress=progress,
        )


def _register_task(dash) -> None:
    total = int(getattr(dash, "_provider_queue_total", 0) or 0)
    task_id = _hook(
        dash,
        "_register_running_task",
        task_type="scan",
        name="Unified Provider Queue",
        state="queued",
        progress=f"0/{total} providers",
        reopen_callback=getattr(dash, "_reopen_scan_output_dialog", None),
        cancel_callback=lambda: cancel_provider_queue(dash, notify=True),
    )
    dash._provider_queue_task_id = task_id


def _remove_task(dash) -> None:
    task_id = getattr(dash, "_provider_queue_task_id", None)
    if task_id:
        _hook(dash, "_remove_running_task", task_id)
    dash._provider_queue_task_id = None


def _has_conflicting_desktop_work(dash) -> bool:
    if any(
        bool(getattr(dash, name, False))
        for name in (
            "_searxng_scan_running",
            "_reddit_scan_running",
            "_reddit_grab_running",
            "_queued_scan_active",
        )
    ):
        return True
    scan_manager = getattr(dash, "scan_manager", None)
    if scan_manager is None:
        return False
    if bool(getattr(scan_manager, "is_scanning", False)):
        return True
    is_scan_active = getattr(scan_manager, "is_scan_active", None)
    try:
        return bool(is_scan_active()) if callable(is_scan_active) else False
    except Exception:
        return True


def start_provider_queue(dash, scan_request: dict) -> bool:
    """Build and start one serial provider queue for a unified scan request."""
    if is_provider_queue_active(dash):
        _mb().showwarning(
            "Provider Queue Busy",
            "A unified provider queue is already running.",
        )
        return False
    if _has_conflicting_desktop_work(dash):
        _mb().showwarning(
            "Provider Busy",
            "A desktop provider scan is already running. Please wait for it to complete.",
        )
        return False

    requested = scan_request.get("providers") or ["shodan"]
    ranked = rank_providers(requested)
    unsupported = [
        str(value or "").strip()
        for value in requested
        if str(value or "").strip().lower() not in PROVIDER_SPECS
    ]
    if unsupported:
        _mb().showerror(
            "Scan Error",
            "Unsupported discovery provider(s): " + ", ".join(unsupported),
        )
        return False
    if not ranked:
        _mb().showerror("Scan Error", "Select at least one discovery provider.")
        return False
    if "shodan" in ranked:
        protocols = [
            str(value or "").strip().lower()
            for value in (scan_request.get("protocols") or [])
            if str(value or "").strip().lower() in {"smb", "ftp", "http"}
        ]
        if not protocols:
            _mb().showerror(
                "Scan Error",
                "Select at least one SMB, FTP, or HTTP protocol for Shodan.",
            )
            return False

    generation = int(getattr(dash, "_provider_queue_generation", 0) or 0) + 1
    dash._provider_queue_generation = generation
    dash._provider_queue_active = True
    dash._provider_queue_pending = list(ranked)
    dash._provider_queue_current = None
    dash._provider_queue_request = dict(scan_request)
    dash._provider_queue_total = len(ranked)
    dash._provider_queue_completed = 0
    dash._provider_queue_failures = []
    dash._provider_queue_shodan_result = None
    dash._provider_queue_shodan_batch = {"probe": [], "extract": []}
    dash._provider_queue_launch_error = ""

    _hook(dash, "_reset_log_output", scan_request.get("country"))
    labels = [PROVIDER_SPECS[key].label for key in ranked]
    _hook(dash, "_log_status_event", "Provider queue: " + " -> ".join(labels))
    _register_task(dash)
    launch_next_provider(dash, generation)
    return True


def launch_next_provider(dash, generation: Optional[int] = None) -> bool:
    """Launch the next provider if the queue generation is still current."""
    current_generation = int(getattr(dash, "_provider_queue_generation", 0) or 0)
    if generation is not None and generation != current_generation:
        return False
    if not is_provider_queue_active(dash):
        return False

    pending = getattr(dash, "_provider_queue_pending", [])
    if not pending:
        _finish_provider_queue(dash)
        return True

    provider = pending.pop(0)
    dash._provider_queue_current = provider
    completed = int(getattr(dash, "_provider_queue_completed", 0) or 0)
    total = int(getattr(dash, "_provider_queue_total", 0) or 0)
    label = PROVIDER_SPECS[provider].label
    _update_task(
        dash,
        state="running",
        progress=f"{completed + 1}/{total} providers: {label}",
    )
    _hook(dash, "_log_status_event", f"Provider queue starting: {label}")

    request = dict(getattr(dash, "_provider_queue_request", {}) or {})
    request["_provider_queue_managed"] = True
    request["_provider_queue_generation"] = current_generation
    dash._provider_queue_launch_error = ""

    try:
        from gui.components import dashboard_scan

        launcher = getattr(dashboard_scan, PROVIDER_SPECS[provider].launcher)
        started = bool(launcher(dash, request))
    except Exception as exc:
        started = False
        error = str(exc)
    else:
        error = str(
            getattr(dash, "_provider_queue_launch_error", "") or "failed to start"
        )

    if not started:
        if provider == "shodan":
            _hook(dash, "_clear_queued_scan_state")
            _hook(dash, "_clear_scan_task")
        complete_provider(
            dash,
            provider,
            current_generation,
            success=False,
            error=error,
        )
    return started


def complete_provider(
    dash,
    provider: str,
    generation: int,
    *,
    success: bool,
    error: str = "",
    result_payload: Optional[dict] = None,
    batch_payload: Optional[dict] = None,
) -> bool:
    """Record one provider completion and advance exactly once."""
    if not is_provider_queue_active(dash):
        return False
    if generation != int(getattr(dash, "_provider_queue_generation", 0) or 0):
        return False
    if provider != getattr(dash, "_provider_queue_current", None):
        return False

    label = PROVIDER_SPECS.get(
        provider, ProviderSpec(provider, provider.title(), 999, "")
    ).label
    if provider == "shodan" and isinstance(result_payload, dict):
        dash._provider_queue_shodan_result = dict(result_payload)
        if isinstance(batch_payload, dict):
            dash._provider_queue_shodan_batch = {
                "probe": list(batch_payload.get("probe") or []),
                "extract": list(batch_payload.get("extract") or []),
            }

    if success:
        _hook(dash, "_log_status_event", f"Provider queue completed: {label}")
    else:
        reason = str(error or "unknown error")
        dash._provider_queue_failures.append({"provider": provider, "reason": reason})
        _hook(dash, "_log_status_event", f"Provider queue failed: {label}: {reason}")

    dash._provider_queue_completed = (
        int(getattr(dash, "_provider_queue_completed", 0) or 0) + 1
    )
    dash._provider_queue_current = None
    completed = int(dash._provider_queue_completed)
    total = int(getattr(dash, "_provider_queue_total", 0) or 0)
    _update_task(dash, state="queued", progress=f"{completed}/{total} providers")

    try:
        dash.parent.after(150, launch_next_provider, dash, generation)
    except (AttributeError, tk.TclError):
        launch_next_provider(dash, generation)
    return True


def cancel_provider_queue(dash, *, notify: bool = False) -> bool:
    """Cancel pending providers and invalidate callbacks from the active generation."""
    if not is_provider_queue_active(dash):
        return False

    current = getattr(dash, "_provider_queue_current", None)
    dash._provider_queue_generation = (
        int(getattr(dash, "_provider_queue_generation", 0) or 0) + 1
    )
    dash._provider_queue_active = False
    dash._provider_queue_pending = []
    dash._provider_queue_current = None
    if current == "shodan":
        _hook(dash, "_clear_queued_scan_state")
        scan_manager = getattr(dash, "scan_manager", None)
        if scan_manager is not None and getattr(scan_manager, "is_scanning", False):
            try:
                scan_manager.interrupt_scan()
            except Exception:
                pass
    if current == "searxng":
        _evt = getattr(dash, "_searxng_cancel_event", None)
        if _evt is not None:
            try:
                _evt.set()
            except Exception:
                pass
    _remove_task(dash)
    _hook(dash, "_log_status_event", "Unified provider queue cancelled.")
    if notify:
        _mb().showinfo(
            "Provider Queue Cancelled",
            "Waiting providers were cancelled. An active in-process provider may finish "
            "its current operation but cannot restart the queue.",
        )
    return True


def _finish_provider_queue(dash) -> None:
    """Show deferred summaries and clear the completed queue."""
    if not is_provider_queue_active(dash):
        return

    batch = getattr(dash, "_provider_queue_shodan_batch", {}) or {}
    probe_rows = list(batch.get("probe") or [])
    extract_rows = list(batch.get("extract") or [])
    if probe_rows:
        _hook(dash, "_show_batch_summary", probe_rows, job_type="probe")
    if extract_rows:
        _hook(dash, "_show_batch_summary", extract_rows, job_type="extract")

    shodan_result = getattr(dash, "_provider_queue_shodan_result", None)
    if isinstance(shodan_result, dict):
        _hook(dash, "_show_scan_results", shodan_result)
    reset_status = getattr(dash, "_reset_scan_status", None)
    if callable(reset_status):
        try:
            dash.parent.after(5000, reset_status)
        except (AttributeError, tk.TclError):
            pass

    failures = list(getattr(dash, "_provider_queue_failures", []) or [])
    completed = int(getattr(dash, "_provider_queue_completed", 0) or 0)
    total = int(getattr(dash, "_provider_queue_total", 0) or 0)
    if failures:
        lines = [
            f"- {PROVIDER_SPECS[item['provider']].label}: {item['reason']}"
            for item in failures
        ]
        _mb().showwarning(
            "Provider Queue Completed With Failures",
            "The remaining providers were still attempted:\n\n" + "\n".join(lines),
        )
    if failures:
        _finished_msg = (
            f"Provider queue finished: {completed}/{total} providers attempted "
            f"({len(failures)} failed)."
        )
    else:
        _finished_msg = (
            f"Provider queue finished: {completed}/{total} providers completed."
        )
    _hook(dash, "_log_status_event", _finished_msg)
    _remove_task(dash)
    dash._provider_queue_last_summary = {
        "completed": completed,
        "total": total,
        "failures": failures,
    }
    dash._provider_queue_active = False
    dash._provider_queue_pending = []
    dash._provider_queue_current = None
