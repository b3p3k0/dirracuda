"""Background events must not reuse the explicit user-open focus paths."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gui.components import dashboard_scan_output_dialog as console
from gui.components import scan_results_dialog as scan_results
from gui.components import dashboard_batch_ops as batch
from gui.utils import background_windows


@pytest.mark.parametrize("hidden", [False, True])
def test_provider_transitions_preserve_console_visibility_and_focus(hidden):
    window = MagicMock()
    window.state.return_value = "withdrawn" if hidden else "normal"
    dash = SimpleNamespace(scan_output_dialog=window, scan_output_title_var=MagicMock())
    for provider in ("SMB", "FTP", "HTTP", "REDDIT", "SEARXNG"):
        console.show_scan_output_dialog(dash, protocol=provider, country="US")
    window.deiconify.assert_not_called()
    window.lift.assert_not_called()
    window.focus_force.assert_not_called()
    window.withdraw.assert_not_called()
    assert dash.scan_output_title_var.set.call_count == 5

    console.reopen_scan_output_dialog(dash)
    window.deiconify.assert_called_once()
    window.lift.assert_called_once()
    window.focus_force.assert_called_once()


def test_results_do_not_grab_focus_or_wait_for_dismissal(monkeypatch):
    window = MagicMock()
    monkeypatch.setattr(scan_results.tk, "Toplevel", lambda _parent: window)
    monkeypatch.setattr(scan_results, "ScrollableBody", lambda _parent: MagicMock())
    display = MagicMock()
    monkeypatch.setattr(scan_results, "show_background_window", display)
    result = scan_results.ScanResultsDialog.__new__(scan_results.ScanResultsDialog)
    result.parent = MagicMock()
    result.theme = MagicMock()
    result.close_button = MagicMock()
    result.result = None
    for method in ("_center_dialog", "_create_header", "_create_summary_section",
                   "_create_details_section", "_create_button_panel", "_setup_event_handlers"):
        setattr(result, method, MagicMock())
    result._create_dialog()
    assert result.show() is None
    window.withdraw.assert_called_once()
    display.assert_called_once_with(window)
    window.grab_set.assert_not_called()
    window.focus_force.assert_not_called()
    result.close_button.focus_set.assert_not_called()
    result.parent.wait_window.assert_not_called()
    window.destroy.assert_not_called()


def test_dashboard_summary_returns_without_modal_wait(monkeypatch):
    builder = MagicMock()
    monkeypatch.setattr(batch, "show_batch_summary_dialog", builder)
    dash = SimpleNamespace(parent=object(), theme=None, _protocol_label_for_result=lambda row: "HTTP")
    batch.show_batch_summary(dash, [{"status": "success"}], job_type="extract")
    assert builder.call_args.kwargs["wait"] is False
    assert builder.call_args.kwargs["modal"] is False


def test_post_scan_work_finishes_without_waiting_for_passive_results():
    calls = []
    results = [{"status": "success"}]
    dash = SimpleNamespace(
        parent=MagicMock(),
        _run_background_fetch=lambda **kw: ({"probe": results, "extract": results}, None),
        _execute_batch_probe=lambda *a, **kw: calls.append("probe") or results,
        _execute_batch_extract=lambda *a, **kw: calls.append("extract") or results,
        _show_batch_summary=lambda *a, **kw: calls.append(kw["job_type"] + " summary"),
        _load_clamav_config=lambda: {},
        _maybe_show_clamav_dialog=MagicMock(),
        _show_scan_results=lambda *a: calls.append("scan summary"),
    )
    payload = batch.run_post_scan_batch_operations(
        dash, {"bulk_probe_enabled": True, "bulk_extract_enabled": True, "allow_insecure_tls": False},
        {"hosts_scanned": 1}, schedule_reset=False,
    )
    assert calls == ["probe", "extract", "extract summary", "probe summary", "scan summary"]
    assert payload == {"probe": results, "extract": results}
    assert dash._maybe_show_clamav_dialog.call_args.kwargs == {"wait": False, "modal": False}


@pytest.mark.parametrize("backend", ["x11", "win32", "aqua"])
def test_initial_display_only_hints_x11_and_never_forces_focus(monkeypatch, backend):
    window = MagicMock()
    window.tk.call.return_value = backend
    hint = MagicMock()
    monkeypatch.setattr(background_windows, "_set_x11_no_activation", hint)
    background_windows.show_background_window(window)
    assert hint.call_count == (1 if backend == "x11" else 0)
    window.transient.assert_called_once_with("")
    window.deiconify.assert_called_once()
    window.lift.assert_not_called()
    window.focus_force.assert_not_called()
    window.focus_set.assert_not_called()
    window.grab_set.assert_not_called()


def test_unavailable_x11_hint_keeps_results_accessible(monkeypatch, caplog):
    window = MagicMock()
    window.tk.call.return_value = "x11"
    monkeypatch.setattr(background_windows, "_set_x11_no_activation", MagicMock(side_effect=OSError("no X11")))
    background_windows.show_background_window(window)
    window.deiconify.assert_called_once()
    window.focus_force.assert_not_called()
    assert "activation hint" in caplog.text


def test_x11_display_closed_when_wrapper_lookup_fails(monkeypatch):
    lib = MagicMock()
    lib.XQueryTree.return_value = 0
    monkeypatch.setattr(background_windows, "_x11", lambda: lib)
    with pytest.raises(OSError, match="wrapper unavailable"):
        background_windows._set_x11_no_activation(MagicMock())
    lib.XCloseDisplay.assert_called_once_with(lib.XOpenDisplay.return_value)
    lib.XChangeProperty.assert_not_called()
