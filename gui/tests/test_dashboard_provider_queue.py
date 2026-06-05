"""Regression tests for serial unified-provider scheduling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from gui.components import dashboard_provider_queue as provider_queue
from gui.components import dashboard_scan


class _ImmediateParent:
    def __init__(self) -> None:
        self.calls = []

    def after(self, delay, callback, *args):
        self.calls.append((delay, callback, args))
        callback(*args)


def _messagebox_recorder():
    calls = {"info": [], "warning": [], "error": []}
    box = SimpleNamespace(
        showinfo=lambda *args, **kwargs: calls["info"].append((args, kwargs)),
        showwarning=lambda *args, **kwargs: calls["warning"].append((args, kwargs)),
        showerror=lambda *args, **kwargs: calls["error"].append((args, kwargs)),
    )
    return box, calls


def _dash():
    dash = SimpleNamespace()
    dash.parent = _ImmediateParent()
    dash.logs = []
    dash.reset_calls = []
    dash.shown_results = []
    dash.batch_results = []
    dash.removed_tasks = []
    dash._reset_log_output = lambda country: dash.reset_calls.append(country)
    dash._log_status_event = lambda message: dash.logs.append(message)
    dash._register_running_task = lambda **_kwargs: "provider-task"
    dash._update_running_task = lambda *_args, **_kwargs: None
    dash._remove_running_task = lambda task_id: dash.removed_tasks.append(task_id)
    dash._show_scan_results = lambda result: dash.shown_results.append(result)
    dash._show_batch_summary = (
        lambda rows, job_type: dash.batch_results.append((job_type, rows))
    )
    dash._reopen_scan_output_dialog = lambda: None
    return dash


def _request(providers):
    return {
        "providers": providers,
        "protocols": ["smb", "ftp", "http"],
        "country": "US",
    }


def test_rank_providers_uses_priority_and_stable_request_order(monkeypatch):
    monkeypatch.setitem(
        provider_queue.PROVIDER_SPECS,
        "future_b",
        provider_queue.ProviderSpec("future_b", "Future B", 100, "unused"),
    )
    monkeypatch.setitem(
        provider_queue.PROVIDER_SPECS,
        "future_a",
        provider_queue.ProviderSpec("future_a", "Future A", 100, "unused"),
    )

    ranked = provider_queue.rank_providers(
        ["future_b", "shodan", "reddit", "future_a", "searxng"]
    )

    assert ranked == ["future_b", "reddit", "future_a", "searxng", "shodan"]


def test_all_providers_launch_strictly_one_at_a_time(monkeypatch):
    dash = _dash()
    launches = []
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or True,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_searxng_scan",
        lambda _dash, _request: launches.append("searxng") or True,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_shodan_provider",
        lambda _dash, _request: launches.append("shodan") or True,
    )

    assert provider_queue.start_provider_queue(
        dash, _request(["shodan", "searxng", "reddit"])
    )
    generation = dash._provider_queue_generation
    assert launches == ["reddit"]

    assert provider_queue.complete_provider(
        dash, "reddit", generation, success=True
    )
    assert launches == ["reddit", "searxng"]

    assert provider_queue.complete_provider(
        dash, "searxng", generation, success=True
    )
    assert launches == ["reddit", "searxng", "shodan"]

    shodan_result = {"protocol": "multi", "status": "completed"}
    assert provider_queue.complete_provider(
        dash,
        "shodan",
        generation,
        success=True,
        result_payload=shodan_result,
    )
    assert dash._provider_queue_active is False
    assert dash.shown_results == [shodan_result]
    assert dash.reset_calls == ["US"]


def test_two_provider_queue_waits_for_current_completion(monkeypatch):
    dash = _dash()
    launches = []
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or True,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_searxng_scan",
        lambda _dash, _request: launches.append("searxng") or True,
    )

    provider_queue.start_provider_queue(dash, _request(["searxng", "reddit"]))

    assert launches == ["reddit"]
    assert dash._provider_queue_current == "reddit"
    assert dash._provider_queue_pending == ["searxng"]


def test_launch_failure_records_error_and_continues(monkeypatch):
    dash = _dash()
    box, calls = _messagebox_recorder()
    launches = []
    monkeypatch.setattr(provider_queue, "_mb", lambda: box)
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or False,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_searxng_scan",
        lambda _dash, _request: launches.append("searxng") or True,
    )

    provider_queue.start_provider_queue(dash, _request(["reddit", "searxng"]))
    generation = dash._provider_queue_generation

    assert launches == ["reddit", "searxng"]
    provider_queue.complete_provider(dash, "searxng", generation, success=True)

    assert dash._provider_queue_active is False
    assert dash._provider_queue_last_summary["failures"] == [
        {"provider": "reddit", "reason": "failed to start"}
    ]
    assert len(calls["warning"]) == 1


def test_queue_start_rejects_conflicting_desktop_provider(monkeypatch):
    dash = _dash()
    dash._reddit_grab_running = True
    box, calls = _messagebox_recorder()
    monkeypatch.setattr(provider_queue, "_mb", lambda: box)

    assert not provider_queue.start_provider_queue(dash, _request(["searxng"]))
    assert calls["warning"]
    assert not getattr(dash, "_provider_queue_active", False)


def test_launch_failure_preserves_specific_provider_error(monkeypatch):
    dash = _dash()
    box, _calls = _messagebox_recorder()
    monkeypatch.setattr(provider_queue, "_mb", lambda: box)

    def reject_launch(current_dash, _request):
        provider_queue.report_launch_error(
            current_dash,
            queue_managed=True,
            title="Reddit Error",
            message="Anonymous Reddit RSS is unavailable.",
        )
        return False

    monkeypatch.setattr(dashboard_scan, "start_reddit_scan", reject_launch)

    provider_queue.start_provider_queue(dash, _request(["reddit"]))

    assert dash._provider_queue_last_summary["failures"] == [
        {
            "provider": "reddit",
            "reason": "Anonymous Reddit RSS is unavailable.",
        }
    ]


def test_failure_callback_continues_to_remaining_provider(monkeypatch):
    dash = _dash()
    box, calls = _messagebox_recorder()
    launches = []
    monkeypatch.setattr(provider_queue, "_mb", lambda: box)
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or True,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_searxng_scan",
        lambda _dash, _request: launches.append("searxng") or True,
    )

    provider_queue.start_provider_queue(dash, _request(["reddit", "searxng"]))
    generation = dash._provider_queue_generation
    provider_queue.complete_provider(
        dash, "reddit", generation, success=False, error="HTTP 429"
    )

    assert launches == ["reddit", "searxng"]
    provider_queue.complete_provider(dash, "searxng", generation, success=True)
    assert calls["warning"]


def test_duplicate_and_stale_callbacks_cannot_advance_twice(monkeypatch):
    dash = _dash()
    launches = []
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or True,
    )
    monkeypatch.setattr(
        dashboard_scan,
        "start_searxng_scan",
        lambda _dash, _request: launches.append("searxng") or True,
    )

    provider_queue.start_provider_queue(dash, _request(["reddit", "searxng"]))
    generation = dash._provider_queue_generation
    assert provider_queue.complete_provider(
        dash, "reddit", generation, success=True
    )
    assert not provider_queue.complete_provider(
        dash, "reddit", generation, success=True
    )
    assert launches == ["reddit", "searxng"]

    provider_queue.cancel_provider_queue(dash)
    assert not provider_queue.complete_provider(
        dash, "searxng", generation, success=True
    )


def test_cancel_clears_pending_and_blocks_restart(monkeypatch):
    dash = _dash()
    launches = []
    monkeypatch.setattr(
        dashboard_scan,
        "start_reddit_scan",
        lambda _dash, _request: launches.append("reddit") or True,
    )

    provider_queue.start_provider_queue(dash, _request(["reddit", "searxng"]))
    old_generation = dash._provider_queue_generation

    assert provider_queue.cancel_provider_queue(dash)
    assert dash._provider_queue_pending == []
    assert dash._provider_queue_active is False
    assert dash._provider_queue_generation > old_generation
    assert launches == ["reddit"]


def test_deferred_shodan_batch_and_result_show_at_queue_finish(monkeypatch):
    dash = _dash()
    monkeypatch.setattr(
        dashboard_scan,
        "start_shodan_provider",
        lambda _dash, _request: True,
    )
    provider_queue.start_provider_queue(dash, _request(["shodan"]))
    generation = dash._provider_queue_generation

    provider_queue.complete_provider(
        dash,
        "shodan",
        generation,
        success=True,
        result_payload={"protocol": "multi"},
        batch_payload={
            "probe": [{"ip_address": "192.0.2.1"}],
            "extract": [{"ip_address": "192.0.2.2"}],
        },
    )

    assert [kind for kind, _rows in dash.batch_results] == ["probe", "extract"]
    assert dash.shown_results == [{"protocol": "multi"}]
