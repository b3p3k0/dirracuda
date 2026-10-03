"""Web UI running indicator for the dashboard status footer.

The running check does an HTTP health probe (and may call systemctl), so it
always runs in a worker thread. Results are applied on the Tk thread and
dropped when a newer refresh or a direct push has superseded them.
"""
from __future__ import annotations

import threading

from gui.components import dashboard_status
from gui.utils.logging_config import get_logger

_logger = get_logger("dashboard")

WEBUI_STATUS_POLL_MS = 30_000


def check_webui_running() -> bool:
    """Return True when the configured Web UI server is running and healthy."""
    try:
        from experimental.webui.service_control import get_status

        try:
            from experimental.webui.config import load_config

            cfg = load_config()
            host, port = cfg.bind_address, cfg.port
        except Exception:
            host, port = "127.0.0.1", 2600
        return bool(get_status(host, port).running)
    except Exception as exc:
        _logger.debug("Web UI status check failed: %s", exc)
        return False


def _apply(dash, running: bool) -> None:
    dash.webui_status_text.set(dashboard_status.compose_webui_status_line(running))


def refresh_webui_status(dash) -> None:
    """Start a background Web UI check unless one is already in flight."""
    if dash._webui_status_busy:
        return
    dash._webui_status_busy = True
    dash._webui_status_generation += 1
    generation = dash._webui_status_generation

    def _worker() -> None:
        running = check_webui_running()
        try:
            dash.parent.after(0, lambda: finish_webui_status(dash, generation, running))
        except Exception:
            # Parent torn down while the worker ran; nothing to update.
            pass

    threading.Thread(target=_worker, name="dashboard-webui-status", daemon=True).start()


def finish_webui_status(dash, generation: int, running: bool) -> None:
    """Apply a worker result if no newer refresh or push superseded it."""
    dash._webui_status_busy = False
    if generation != dash._webui_status_generation:
        return
    _apply(dash, running)


def set_webui_status(dash, running: bool) -> None:
    """Direct push from the Web UI tab after Start/Stop (Tk thread only)."""
    dash._webui_status_generation += 1
    _apply(dash, running)


def start_webui_status_poll(dash) -> None:
    """Re-check the Web UI state every WEBUI_STATUS_POLL_MS until stopped."""
    dash._webui_status_poll_stopped = False

    def _tick() -> None:
        dash._webui_status_after_id = None
        if dash._webui_status_poll_stopped:
            return
        try:
            refresh_webui_status(dash)
        except Exception as exc:
            _logger.debug("Web UI status poll failed: %s", exc)
        dash._webui_status_after_id = dash.parent.after(WEBUI_STATUS_POLL_MS, _tick)

    dash._webui_status_after_id = dash.parent.after(WEBUI_STATUS_POLL_MS, _tick)


def stop_webui_status_poll(dash) -> None:
    """Cancel the poll loop; safe to call more than once."""
    dash._webui_status_poll_stopped = True
    after_id = getattr(dash, "_webui_status_after_id", None)
    if after_id is not None:
        try:
            dash.parent.after_cancel(after_id)
        except Exception:
            pass
    dash._webui_status_after_id = None
