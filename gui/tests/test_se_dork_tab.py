"""
Unit tests for gui.components.experimental_features.se_dork_tab.

Uses __new__ to bypass Tk construction — no display required.
Threading is monkeypatched to run synchronously.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Lightweight impacket stub so GUI modules import cleanly in headless test env.
if "impacket" not in sys.modules:
    _imod = types.ModuleType("impacket")
    _ismb = types.ModuleType("impacket.smb")
    _ismb.SMB2_DIALECT_002 = object()
    _iconn = types.ModuleType("impacket.smbconnection")
    _iconn.SMBConnection = object

    class _SessionError(Exception):
        pass

    _iconn.SessionError = _SessionError
    _imod.smb = _ismb
    sys.modules["impacket"] = _imod
    sys.modules["impacket.smb"] = _ismb
    sys.modules["impacket.smbconnection"] = _iconn

from experimental.se_dork.models import PreflightResult
from gui.components.experimental_features.se_dork_tab import (
    SeDorkTab,
    _resolve_initial_url,
    _resolve_probe_worker_count,
    _DEFAULT_INSTANCE_URL,
    _SETTINGS_KEY_URL,
    _SETTINGS_KEY_BULK_PROBE_ENABLED,
)


def test_build_uses_updated_labels_and_max_helper_text(monkeypatch):
    texts = []

    class _DummyVar:
        def __init__(self, value=None):
            self._value = value

        def get(self):
            return self._value

        def set(self, value):
            self._value = value

    class _DummyWidget:
        def __init__(self, *args, **kwargs):
            text = kwargs.get("text")
            if text is not None:
                texts.append(text)

        def pack(self, *args, **kwargs):
            return None

        def configure(self, *args, **kwargs):
            return None

        def bind(self, *args, **kwargs):
            return None

    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.Frame", _DummyWidget)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.Label", _DummyWidget)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.Entry", _DummyWidget)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.Checkbutton", _DummyWidget)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.Button", _DummyWidget)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.StringVar", _DummyVar)
    monkeypatch.setattr("gui.components.experimental_features.se_dork_tab.tk.BooleanVar", _DummyVar)

    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = {}
    tab._theme = MagicMock()
    tab._theme.apply_to_widget = lambda *a, **kw: None

    tab._build(_DummyWidget())

    assert "SearXNG Server:" in texts
    assert "Run Probe on Results" in texts
    assert "Maximum 500" in texts


# ---------------------------------------------------------------------------
# Pure helper: _resolve_initial_url
# ---------------------------------------------------------------------------

def test_resolve_initial_url_uses_settings():
    sm = MagicMock()
    sm.get_setting.return_value = "http://saved:9000"
    result = _resolve_initial_url(sm, _DEFAULT_INSTANCE_URL)
    assert result == "http://saved:9000"
    sm.get_setting.assert_called_once_with(_SETTINGS_KEY_URL, _DEFAULT_INSTANCE_URL)


def test_resolve_initial_url_falls_back_to_default():
    result = _resolve_initial_url(None, _DEFAULT_INSTANCE_URL)
    assert result == _DEFAULT_INSTANCE_URL


def test_resolve_initial_url_falls_back_when_sm_returns_empty():
    sm = MagicMock()
    sm.get_setting.return_value = ""
    result = _resolve_initial_url(sm, _DEFAULT_INSTANCE_URL)
    assert result == _DEFAULT_INSTANCE_URL


def test_resolve_initial_url_survives_sm_exception():
    sm = MagicMock()
    sm.get_setting.side_effect = RuntimeError("boom")
    result = _resolve_initial_url(sm, _DEFAULT_INSTANCE_URL)
    assert result == _DEFAULT_INSTANCE_URL


def test_resolve_probe_worker_count_uses_settings():
    sm = MagicMock()
    sm.get_setting.return_value = 6
    assert _resolve_probe_worker_count(sm) == 6


def test_resolve_probe_worker_count_clamps_and_falls_back():
    sm = MagicMock()
    sm.get_setting.return_value = 99
    assert _resolve_probe_worker_count(sm) == 8
    sm.get_setting.return_value = "bad"
    assert _resolve_probe_worker_count(sm) == 3
    assert _resolve_probe_worker_count(None) == 3


# ---------------------------------------------------------------------------
# _save_url helper
# ---------------------------------------------------------------------------

def _make_tab(context: dict) -> SeDorkTab:
    """Construct SeDorkTab without Tk using __new__."""
    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = context
    tab._url_var = MagicMock()
    tab._url_var.get.return_value = "http://test:8090"
    return tab


def test_save_url_calls_settings_manager():
    sm = MagicMock()
    tab = _make_tab({"settings_manager": sm})
    tab._save_url()
    sm.set_setting.assert_called_once_with(_SETTINGS_KEY_URL, "http://test:8090")


def test_save_url_noop_when_no_settings_manager():
    tab = _make_tab({})
    tab._save_url()  # must not raise


def test_save_url_noop_when_sm_is_none():
    tab = _make_tab({"settings_manager": None})
    tab._save_url()  # must not raise


def test_save_url_survives_sm_exception():
    sm = MagicMock()
    sm.set_setting.side_effect = RuntimeError("disk full")
    tab = _make_tab({"settings_manager": sm})
    tab._save_url()  # must not raise


def test_save_settings_persists_bulk_probe_flag():
    sm = MagicMock()
    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = {"settings_manager": sm}
    tab._url_var = MagicMock(get=MagicMock(return_value="http://test:8090"))
    tab._query_var = MagicMock(get=MagicMock(return_value="site:*"))
    tab._max_results_var = MagicMock(get=MagicMock(return_value="10"))
    tab._bulk_probe_var = MagicMock(get=MagicMock(return_value=True))

    tab._save_settings()

    sm.set_setting.assert_any_call(_SETTINGS_KEY_BULK_PROBE_ENABLED, True)


# ---------------------------------------------------------------------------
# _invoke_test wiring (thread runs synchronously via monkeypatch)
# ---------------------------------------------------------------------------

def _make_wired_tab(context: dict, url: str = "http://test:8090") -> SeDorkTab:
    """Tab with enough state to exercise _invoke_test."""
    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = context
    tab._url_var = MagicMock()
    tab._url_var.get.return_value = url
    tab._test_btn = MagicMock()
    tab._status_label = MagicMock()
    tab.frame = MagicMock()
    # Make after() call its callback immediately (synchronous)
    tab.frame.after = lambda delay, fn: fn()
    return tab


def test_invoke_test_calls_preflight(monkeypatch):
    calls = []
    fake_result = PreflightResult(ok=True, reason_code=None, message="Instance OK.")

    monkeypatch.setattr(
        "experimental.se_dork.client.run_preflight",
        lambda url, **kw: calls.append(url) or fake_result,
    )
    # Run thread synchronously
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )

    tab = _make_wired_tab({})
    tab._invoke_test()

    assert calls == ["http://test:8090"]


def test_invoke_test_updates_status_on_success(monkeypatch):
    fake_result = PreflightResult(ok=True, reason_code=None, message="Instance OK.")

    monkeypatch.setattr(
        "experimental.se_dork.client.run_preflight",
        lambda url, **kw: fake_result,
    )
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )

    tab = _make_wired_tab({})
    tab._invoke_test()

    configure_calls = tab._status_label.configure.call_args_list
    final_text = configure_calls[-1][1].get("text", "")
    assert "OK" in final_text or "\u2713" in final_text


def test_invoke_test_updates_status_on_failure(monkeypatch):
    from experimental.se_dork.models import INSTANCE_UNREACHABLE
    fake_result = PreflightResult(
        ok=False,
        reason_code=INSTANCE_UNREACHABLE,
        message="Cannot reach instance: connection refused.",
    )

    monkeypatch.setattr(
        "experimental.se_dork.client.run_preflight",
        lambda url, **kw: fake_result,
    )
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )

    tab = _make_wired_tab({})
    tab._invoke_test()

    configure_calls = tab._status_label.configure.call_args_list
    final_text = configure_calls[-1][1].get("text", "")
    assert "connection refused" in final_text or "\u2717" in final_text


def test_invoke_test_handles_unexpected_worker_exception(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.client.run_preflight",
        lambda url, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )

    tab = _make_wired_tab({})
    tab._invoke_test()

    configure_calls = tab._status_label.configure.call_args_list
    final_text = configure_calls[-1][1].get("text", "")
    assert "Unexpected preflight error" in final_text

    btn_calls = [c[1] for c in tab._test_btn.configure.call_args_list]
    states = [c.get("state") for c in btn_calls if "state" in c]
    assert "disabled" in states
    assert states[-1] == "normal"


def test_invoke_test_disables_button_then_reenables(monkeypatch):
    fake_result = PreflightResult(ok=True, reason_code=None, message="Instance OK.")

    monkeypatch.setattr(
        "experimental.se_dork.client.run_preflight",
        lambda url, **kw: fake_result,
    )
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )

    tab = _make_wired_tab({})
    tab._invoke_test()

    configure_calls = [c[1] for c in tab._test_btn.configure.call_args_list]
    states = [c.get("state") for c in configure_calls if "state" in c]
    assert "disabled" in states
    assert states[-1] == "normal"


def test_invoke_test_empty_url_sets_status_without_thread(monkeypatch):
    thread_started = []

    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: thread_started.append(True)),
    )

    tab = _make_wired_tab({}, url="")
    tab._invoke_test()

    assert thread_started == []
    tab._status_label.configure.assert_called()


# ---------------------------------------------------------------------------
# C3: _invoke_run wiring
# ---------------------------------------------------------------------------

def _make_run_tab(context: dict, url: str = "http://test:8090", query: str = 'site:*') -> "SeDorkTab":
    """Tab with enough state to exercise _invoke_run."""
    tab = SeDorkTab.__new__(SeDorkTab)
    tab._context = context
    tab._url_var = MagicMock()
    tab._url_var.get.return_value = url
    tab._query_var = MagicMock()
    tab._query_var.get.return_value = query
    tab._max_results_var = MagicMock()
    tab._max_results_var.get.return_value = "10"
    tab._bulk_probe_var = MagicMock()
    tab._bulk_probe_var.get.return_value = False
    tab._test_btn = MagicMock()
    tab._run_btn = MagicMock()
    tab._status_label = MagicMock()
    tab.frame = MagicMock()
    tab.frame.after = lambda delay, fn: fn()
    return tab


def _sync_thread(monkeypatch) -> None:
    """Patch threading.Thread to run the target synchronously."""
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: target()),
    )


def _fake_run_result(
    status: str = "done",
    fetched: int = 5,
    deduped: int = 4,
    error=None,
    *,
    probe_enabled: bool = False,
    probe_total: int = 0,
    probe_clean: int = 0,
    probe_issue: int = 0,
    probe_unprobed: int = 0,
):
    from experimental.se_dork.models import RunResult
    return RunResult(
        run_id=1 if status == "done" else None,
        fetched_count=fetched,
        deduped_count=deduped,
        status=status,
        error=error,
        probe_enabled=probe_enabled,
        probe_total=probe_total,
        probe_clean=probe_clean,
        probe_issue=probe_issue,
        probe_unprobed=probe_unprobed,
    )


def test_invoke_run_calls_service(monkeypatch):
    calls = []
    fake_result = _fake_run_result()
    sm = MagicMock()
    sm.get_smbseek_config_path.return_value = "/tmp/smbseek.json"
    sm.get_setting.return_value = 6

    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: calls.append(opts) or fake_result,
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({"settings_manager": sm})
    tab._bulk_probe_var.get.return_value = True
    tab._invoke_run()

    assert len(calls) == 1
    assert calls[0].instance_url == "http://test:8090"
    assert calls[0].query == "site:*"
    assert calls[0].max_results == 10
    assert calls[0].bulk_probe_enabled is True
    assert calls[0].probe_config_path == "/tmp/smbseek.json"
    assert calls[0].probe_worker_count == 6


def test_invoke_run_updates_status_on_success(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: _fake_run_result(status="done", fetched=5, deduped=4),
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({})
    tab._invoke_run()

    final_text = tab._status_label.configure.call_args_list[-1][1].get("text", "")
    assert "5" in final_text
    assert "4" in final_text


def test_invoke_run_includes_probe_summary_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: _fake_run_result(
            status="done",
            fetched=5,
            deduped=2,
            probe_enabled=True,
            probe_total=2,
            probe_clean=1,
            probe_issue=1,
            probe_unprobed=0,
        ),
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({})
    tab._bulk_probe_var.get.return_value = True
    tab._invoke_run()

    final_text = tab._status_label.configure.call_args_list[-1][1].get("text", "")
    assert "Probe:" in final_text
    assert "✔ 1" in final_text
    assert "✖ 1" in final_text
    assert "○ 0" in final_text


def test_invoke_run_updates_status_on_failure(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: _fake_run_result(status="error", error="network refused"),
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({})
    tab._invoke_run()

    final_text = tab._status_label.configure.call_args_list[-1][1].get("text", "")
    assert "network refused" in final_text


def test_invoke_run_disables_both_buttons_then_reenables(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: _fake_run_result(),
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({})
    tab._invoke_run()

    test_states = [c[1].get("state") for c in tab._test_btn.configure.call_args_list if "state" in c[1]]
    run_states = [c[1].get("state") for c in tab._run_btn.configure.call_args_list if "state" in c[1]]

    assert "disabled" in test_states
    assert test_states[-1] == "normal"
    assert "disabled" in run_states
    assert run_states[-1] == "normal"


def test_invoke_run_exception_in_thread_reenables_buttons(monkeypatch):
    monkeypatch.setattr(
        "experimental.se_dork.service.run_dork_search",
        lambda opts, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    _sync_thread(monkeypatch)

    tab = _make_run_tab({})
    tab._invoke_run()

    run_states = [c[1].get("state") for c in tab._run_btn.configure.call_args_list if "state" in c[1]]
    assert run_states[-1] == "normal"
    final_text = tab._status_label.configure.call_args_list[-1][1].get("text", "")
    assert "failed" in final_text.lower() or "boom" in final_text


def test_invoke_run_empty_url_no_thread(monkeypatch):
    thread_started = []
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: thread_started.append(True)),
    )

    tab = _make_run_tab({}, url="")
    tab._invoke_run()

    assert thread_started == []
    tab._status_label.configure.assert_called()


def test_invoke_run_empty_query_no_thread(monkeypatch):
    thread_started = []
    monkeypatch.setattr(
        "gui.components.experimental_features.se_dork_tab.threading.Thread",
        lambda target, daemon=True: MagicMock(start=lambda: thread_started.append(True)),
    )

    tab = _make_run_tab({}, query="")
    tab._invoke_run()

    assert thread_started == []
    tab._status_label.configure.assert_called()
