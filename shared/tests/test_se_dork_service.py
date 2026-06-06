"""
Unit tests for experimental.se_dork.service.

Network (urllib.request.urlopen) and preflight are mocked throughout.
DB tests use tmp_path for isolation.
"""

from __future__ import annotations

import io
import json
import sqlite3
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from experimental.se_dork.models import (
    RunOptions,
    RUN_STATUS_DONE,
    RUN_STATUS_ERROR,
)
from experimental.se_dork.service import (
    _HARD_PAGE_CAP,
    _fetch_results,
    _paginate_results,
    run_dork_search,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _options(**kwargs) -> RunOptions:
    defaults = dict(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results=10,
    )
    defaults.update(kwargs)
    return RunOptions(**defaults)


def _ok_preflight():
    return SimpleNamespace(ok=True, reason_code=None, message="Instance OK.")


def _fail_preflight(reason="instance_unreachable", msg="Cannot reach instance."):
    return SimpleNamespace(ok=False, reason_code=reason, message=msg)


def _searxng_response(
    results: list,
    *,
    unresponsive_engines=None,
) -> MagicMock:
    """Return a mock urlopen context manager yielding SearXNG JSON."""
    payload = {"results": results}
    if unresponsive_engines is not None:
        payload["unresponsive_engines"] = unresponsive_engines
    body = json.dumps(payload).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.status = 200
    cm.read = MagicMock(return_value=body)
    return cm


def _results_rows(n: int) -> list:
    return [
        {"url": f"http://example.com/dir{i}/", "title": f"Dir {i}", "content": "", "engine": "bing", "engines": ["bing"]}
        for i in range(n)
    ]


def _open_index_result():
    from experimental.se_dork.classifier import ClassifyResult, VERDICT_OPEN_INDEX
    return ClassifyResult(verdict=VERDICT_OPEN_INDEX, reason_code=None, http_status=200)


def _noise_result():
    from experimental.se_dork.classifier import ClassifyResult, VERDICT_NOISE
    return ClassifyResult(verdict=VERDICT_NOISE, reason_code="http_404", http_status=404)


def _probe_outcome(
    status: str = "clean",
    *,
    matches: int = 0,
    preview: str | None = None,
    checked_at: str = "2026-01-01T00:10:00",
    error: str | None = None,
    snapshot: dict | None = None,
):
    from experimental.se_dork.probe import ProbeOutcome

    return ProbeOutcome(
        probe_status=status,
        probe_indicator_matches=matches,
        probe_preview=preview,
        probe_checked_at=checked_at,
        probe_error=error,
        probe_snapshot_payload=snapshot,
    )


@pytest.fixture(autouse=True)
def _skip_real_pacing_waits(monkeypatch):
    monkeypatch.setattr("experimental.se_dork.service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "experimental.se_dork.service.random.uniform",
        lambda low, high: (low + high) / 2,
    )


# ---------------------------------------------------------------------------
# Preflight failure
# ---------------------------------------------------------------------------


def test_run_dork_search_reachability_fail(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_fail_preflight()):
        result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Reachability failed" in result.error
    # sidecar DB must not have been created
    assert not (tmp_path / "se_dork.db").exists()


# ---------------------------------------------------------------------------
# DB setup failure
# ---------------------------------------------------------------------------


def test_run_dork_search_db_setup_failure(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("experimental.se_dork.service.init_db", side_effect=RuntimeError("disk full")):
            result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "DB setup failed" in result.error


# ---------------------------------------------------------------------------
# Network error after preflight
# ---------------------------------------------------------------------------


def test_run_dork_search_network_error(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is not None  # run row was committed before network I/O
    assert result.fetched_count == 0


def test_later_network_error_keeps_completed_page_as_partial_success(
    tmp_path: Path,
) -> None:
    db = tmp_path / "se_dork.db"
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _searxng_response(_results_rows(1)),
                urllib.error.URLError("connection reset"),
            ],
        ):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 1
    assert result.deduped_count == 1
    assert result.stopped_early is True
    assert result.fetch_warning and "connection reset" in result.fetch_warning
    with sqlite3.connect(str(db)) as conn:
        run_status = conn.execute(
            "SELECT status FROM dork_runs WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
        retained = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
    assert run_status == RUN_STATUS_DONE
    assert retained == 1


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_run_dork_search_success(tmp_path: Path) -> None:
    rows = _results_rows(5)
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        # First call returns 5 results; second call returns empty (stops pagination)
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=10), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.run_id is not None
    assert result.fetched_count == 5
    assert result.deduped_count == 5
    assert result.error is None
    assert result.probe_enabled is False
    assert result.probe_total == 0


def test_normal_run_uses_config_then_real_query_without_hello(
    tmp_path: Path,
) -> None:
    responses = [
        _searxng_response([]),
        _searxng_response(_results_rows(1)),
    ]
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        with patch(
            "experimental.se_dork.classifier.classify_url",
            return_value=_open_index_result(),
        ):
            result = run_dork_search(
                _options(max_results=1),
                db_path=tmp_path / "se_dork.db",
            )

    requested = [call.args[0] for call in mock_open.call_args_list]
    assert result.status == RUN_STATUS_DONE
    assert requested[0].endswith("/config")
    assert "pageno=1" in requested[1]
    assert all("q=hello" not in url for url in requested)


def test_run_dork_search_success_persists_to_db(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    with sqlite3.connect(str(db)) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM dork_runs").fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM dork_results").fetchone()[0]
    assert run_count == 1
    assert result_count == 3


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def test_run_dork_search_dedupe(tmp_path: Path) -> None:
    rows = [
        {"url": "http://example.com/files/", "title": "A"},
        {"url": "http://example.com/files",  "title": "A duplicate"},  # same normalized
    ]
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=10), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 1
    assert result.deduped_count == 1


# ---------------------------------------------------------------------------
# max_results cap
# ---------------------------------------------------------------------------


def test_run_dork_search_respects_max_results(tmp_path: Path) -> None:
    rows = _results_rows(5)
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", return_value=_searxng_response(rows)):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=3), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 3
    assert result.deduped_count == 3


def test_run_dork_search_clamps_zero_max_results(tmp_path: Path) -> None:
    rows = _results_rows(2)
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            # max_results=0 is clamped to 1 internally
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(_options(max_results=0), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 1


# ---------------------------------------------------------------------------
# Run row is durable after error (COMMIT 1 before network I/O)
# ---------------------------------------------------------------------------


def test_run_row_persisted_after_network_error(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = run_dork_search(_options(), db_path=db)

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is not None
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT status, error_message FROM dork_runs WHERE run_id=?",
            (result.run_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == RUN_STATUS_ERROR


# ---------------------------------------------------------------------------
# C4: Classification integration
# ---------------------------------------------------------------------------


def test_run_dork_search_classifies_results(tmp_path: Path) -> None:
    """Each fetched row is classified and contributes to verified_count."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ) as mock_classify:
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.verified_count == 3
    assert mock_classify.call_count == 3


def test_run_dork_search_updates_verified_count(tmp_path: Path) -> None:
    """verified_count in DB is updated by classification phase."""
    from experimental.se_dork.classifier import VERDICT_OPEN_INDEX
    from experimental.se_dork.classifier import ClassifyResult

    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    fake_result = ClassifyResult(
        verdict=VERDICT_OPEN_INDEX, reason_code=None, http_status=200
    )

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.try_http_request",
                return_value=(200, "<title>Index of /</title><a href='f'>f</a>", False, ""),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.verified_count == 2
    with sqlite3.connect(str(db)) as conn:
        vc = conn.execute(
            "SELECT verified_count FROM dork_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert vc == 2


def test_run_dork_search_drops_non_open_results(tmp_path: Path) -> None:
    """Rows classified as non-open are removed from dork_results and run summary."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_noise_result(),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 3
    assert result.deduped_count == 0
    with sqlite3.connect(str(db)) as conn:
        result_rows = conn.execute("SELECT COUNT(*) FROM dork_results").fetchone()[0]
        deduped_count = conn.execute(
            "SELECT deduped_count FROM dork_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert result_rows == 0
    assert deduped_count == 0


def test_run_dork_search_deduped_count_matches_retained_open_index(tmp_path: Path) -> None:
    """RunResult.deduped_count and dork_runs.deduped_count must match retained OPEN_INDEX rows."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)
    side_effects = [_open_index_result(), _noise_result()]

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                side_effect=side_effects,
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.deduped_count == 1
    with sqlite3.connect(str(db)) as conn:
        persisted_run_deduped = conn.execute(
            "SELECT deduped_count FROM dork_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
        retained_rows = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert persisted_run_deduped == 1
    assert retained_rows == 1


def test_run_dork_search_classification_failure_does_not_fail_run(tmp_path: Path) -> None:
    """Unexpected per-row classifier errors become removable ERROR verdicts."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
            ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                side_effect=RuntimeError("classify boom"),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.verified_count == 2
    assert result.deduped_count == 0


def test_classification_and_probe_network_calls_hold_no_db_connection(
    tmp_path: Path,
) -> None:
    from experimental.se_dork import service as service_module

    db = tmp_path / "se_dork.db"
    active_connections = [0]
    real_open_connection = service_module.open_connection

    class _TrackedConnection:
        def __init__(self, connection):
            self._connection = connection
            self._closed = False
            active_connections[0] += 1

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            if not self._closed:
                self._closed = True
                active_connections[0] -= 1
            return self._connection.close()

    def _tracked_open(path):
        return _TrackedConnection(real_open_connection(path))

    def _classify(_url, timeout=10.0):
        assert active_connections[0] == 0
        return _open_index_result()

    def _probe(*args, **kwargs):
        assert active_connections[0] == 0
        return _probe_outcome(status="clean")

    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _searxng_response(_results_rows(1)),
                _searxng_response([]),
            ],
        ):
            with patch(
                "experimental.se_dork.service.open_connection",
                side_effect=_tracked_open,
            ):
                with patch(
                    "experimental.se_dork.classifier.classify_url",
                    side_effect=_classify,
                ):
                    with patch(
                        "experimental.se_dork.probe.build_indicator_patterns",
                        return_value=[],
                    ):
                        with patch(
                            "experimental.se_dork.probe.probe_url",
                            side_effect=_probe,
                        ):
                            result = run_dork_search(
                                _options(
                                    max_results=10,
                                    bulk_probe_enabled=True,
                                ),
                                db_path=db,
                            )

    assert result.status == RUN_STATUS_DONE
    assert active_connections[0] == 0


def test_second_page_storage_failure_keeps_first_page_and_marks_error(
    tmp_path: Path,
) -> None:
    from experimental.se_dork.store import insert_page_results as real_insert_page

    db = tmp_path / "se_dork.db"
    insert_calls = [0]

    def _insert_page(conn, run_id, rows):
        insert_calls[0] += 1
        if insert_calls[0] == 2:
            raise RuntimeError("disk write failed")
        return real_insert_page(conn, run_id, rows)

    responses = [
        _searxng_response([{"url": "http://example.com/one/"}]),
        _searxng_response([{"url": "http://example.com/two/"}]),
    ]
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch("urllib.request.urlopen", side_effect=responses):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.service.insert_page_results",
                    side_effect=_insert_page,
                ):
                    result = run_dork_search(
                        _options(max_results=10),
                        db_path=db,
                    )

    assert result.status == RUN_STATUS_ERROR
    assert result.fetched_count == 1
    assert result.deduped_count == 1
    assert "Page 2 processing failed" in result.error
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT url FROM dork_results WHERE run_id = ?",
            (result.run_id,),
        ).fetchall()
        status = conn.execute(
            "SELECT status FROM dork_runs WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
    assert rows == [("http://example.com/one/",)]
    assert status == RUN_STATUS_ERROR


def test_classification_persistence_failure_cleans_unclassified_page(
    tmp_path: Path,
) -> None:
    db = tmp_path / "se_dork.db"
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch(
            "urllib.request.urlopen",
            return_value=_searxng_response(_results_rows(1)),
        ):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.service.update_result_verdict",
                    side_effect=RuntimeError("database locked"),
                ):
                    result = run_dork_search(
                        _options(max_results=1),
                        db_path=db,
                    )

    assert result.status == RUN_STATUS_ERROR
    assert result.fetched_count == 0
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
    assert rows == 0


def test_run_dork_search_commit1_exception_returns_structured_error(tmp_path: Path) -> None:
    """If insert_run raises, run_dork_search returns ERROR with run_id=None."""
    from experimental.se_dork.store import insert_run as real_insert_run

    db = tmp_path / "se_dork.db"

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch(
            "experimental.se_dork.service.insert_run",
            side_effect=RuntimeError("disk full"),
        ):
            result = run_dork_search(_options(), db_path=db)

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Run setup failed" in result.error


def test_run_dork_search_persists_clamped_max_results(tmp_path: Path) -> None:
    """dork_runs.max_results must store the clamped value, not raw user input."""
    db = tmp_path / "se_dork.db"

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response([]),
        ]):
            result = run_dork_search(_options(max_results=9999), db_path=db)

    assert result.status == RUN_STATUS_DONE
    with sqlite3.connect(str(db)) as conn:
        persisted = conn.execute(
            "SELECT max_results FROM dork_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert persisted == 1000


def test_fetch_results_continues_beyond_page_ten() -> None:
    responses = [
        _searxng_response([{
            "url": f"http://example.com/page{page}/",
            "title": f"Page {page}",
        }])
        for page in range(1, 13)
    ]
    responses.append(_searxng_response([]))

    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        outcome = _fetch_results("http://searxng.local", "index of", 1000)

    assert len(outcome.rows) == 12
    assert outcome.pages_fetched == 13
    assert "pageno=11" in mock_open.call_args_list[10].args[0]


def test_fetch_results_stops_at_hard_page_cap() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_searxng_response([{"url": "http://example.com/repeated/"}]),
    ) as mock_open:
        outcome = _fetch_results("http://searxng.local", "index of", 1000)

    assert len(outcome.rows) == 1
    assert outcome.pages_fetched == _HARD_PAGE_CAP
    assert mock_open.call_count == _HARD_PAGE_CAP == 40


def test_fetch_results_counts_unique_urls_toward_requested_budget() -> None:
    page_one = [
        {"url": "http://example.com/files/"},
        {"url": "http://example.com/files"},
    ]
    page_two = [{"url": "http://example.com/other/"}]

    with patch("urllib.request.urlopen", side_effect=[
        _searxng_response(page_one),
        _searxng_response(page_two),
    ]) as mock_open:
        outcome = _fetch_results("http://searxng.local", "index of", 2)

    assert [row["url"] for row in outcome.rows] == [
        "http://example.com/files/",
        "http://example.com/other/",
    ]
    assert mock_open.call_count == 2


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (1, 0.0),
        (2, 2.0),
        (5, 2.0),
        (6, 4.0),
        (10, 4.0),
        (11, 6.0),
        (20, 6.0),
        (21, 8.0),
        (40, 8.0),
    ],
)
def test_page_delay_uses_adaptive_tiers(page: int, expected: float) -> None:
    from experimental.se_dork.service import _page_delay_seconds

    assert _page_delay_seconds(
        page,
        jitter_fn=lambda low, high: (low + high) / 2,
    ) == expected


def test_page_delay_jitter_bounds() -> None:
    from experimental.se_dork.service import _page_delay_seconds

    bounds = []
    _page_delay_seconds(11, jitter_fn=lambda low, high: bounds.append((low, high)) or low)
    assert bounds[0] == pytest.approx((4.8, 7.2))


def test_fetch_results_does_not_sleep_before_first_or_after_limit() -> None:
    sleeps = []
    with patch(
        "urllib.request.urlopen",
        return_value=_searxng_response(_results_rows(2)),
    ):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            2,
            sleep_fn=sleeps.append,
        )

    assert len(outcome.rows) == 2
    assert sleeps == []
    assert outcome.pacing_delay_seconds == 0


def test_page_processor_finishes_before_next_page_request() -> None:
    events = []
    responses = [
        _searxng_response(_results_rows(1)),
        _searxng_response([]),
    ]

    def _urlopen(*args, **kwargs):
        page = len([event for event in events if event.startswith("fetch")]) + 1
        events.append(f"fetch:{page}")
        return responses.pop(0)

    def _process(page, rows):
        events.append(f"process:{page}:{len(rows)}")

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        _paginate_results(
            "http://searxng.local",
            "index of",
            10,
            page_processor=_process,
            collect_rows=False,
            sleep_fn=lambda _seconds: None,
        )

    assert events == ["fetch:1", "process:1:1", "fetch:2"]


def test_page_processing_consumes_soft_backoff_window() -> None:
    clock = [100.0]
    sleeps = []
    responses = [
        _searxng_response(
            _results_rows(1),
            unresponsive_engines=[["bing", "HTTP error 429"]],
        ),
        _searxng_response([]),
    ]

    def _process(_page, _rows):
        clock[0] += 7.0

    def _sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    with patch("urllib.request.urlopen", side_effect=responses):
        outcome = _paginate_results(
            "http://searxng.local",
            "index of",
            10,
            page_processor=_process,
            collect_rows=False,
            sleep_fn=_sleep,
            monotonic_fn=lambda: clock[0],
        )

    assert sleeps == [3.0]
    assert outcome.pacing_delay_seconds == 3.0


def test_page_processing_that_exceeds_window_skips_sleep() -> None:
    clock = [100.0]
    sleeps = []
    responses = [
        _searxng_response(
            _results_rows(1),
            unresponsive_engines=[["bing", "HTTP error 429"]],
        ),
        _searxng_response([]),
    ]

    def _process(_page, _rows):
        clock[0] += 12.0

    with patch("urllib.request.urlopen", side_effect=responses):
        outcome = _paginate_results(
            "http://searxng.local",
            "index of",
            10,
            page_processor=_process,
            collect_rows=False,
            sleep_fn=sleeps.append,
            monotonic_fn=lambda: clock[0],
        )

    assert sleeps == []
    assert outcome.pacing_delay_seconds == 0


def test_result_cap_does_not_report_unused_soft_backoff() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_searxng_response(
            _results_rows(2),
            unresponsive_engines=[["bing", "too many requests"]],
        ),
    ):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            2,
            sleep_fn=lambda _seconds: None,
        )

    assert len(outcome.rows) == 2
    assert outcome.throttled_page_count == 0
    assert outcome.warning is None


@pytest.mark.parametrize(
    "unresponsive",
    [
        [["bing", "HTTP error 429"]],
        [["google", "Too many requests"]],
        {"brave": "Access denied"},
        [{"engine": "qwant", "error": "Cloudflare CAPTCHA"}],
        [["startpage", "reCAPTCHA challenge"]],
        [["duckduckgo", "HTTP error 403"]],
    ],
)
def test_fetch_results_detects_upstream_throttle_shapes(unresponsive) -> None:
    responses = [
        _searxng_response(
            [{"url": "http://example.com/one/"}],
            unresponsive_engines=unresponsive,
        ),
        _searxng_response([]),
    ]
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=responses):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert outcome.throttled_page_count == 1
    assert outcome.hard_retry_count == 0
    assert sleeps == pytest.approx([10.0], abs=0.01)
    assert outcome.stopped_early is False
    assert outcome.warning and "soft backoff" in outcome.warning


def test_nonempty_throttled_page_advances_without_same_page_retry() -> None:
    responses = [
        _searxng_response(
            [{"url": "http://example.com/one/"}],
            unresponsive_engines=[["bing", "HTTP error 429"]],
        ),
        _searxng_response([]),
    ]
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    requested = [call.args[0] for call in mock_open.call_args_list]
    assert "pageno=1" in requested[0]
    assert "pageno=2" in requested[1]
    assert len(outcome.rows) == 1
    assert outcome.pages_fetched == 2
    assert outcome.throttled_page_count == 1
    assert outcome.hard_retry_count == 0
    assert outcome.throttle_engines == ("bing",)
    assert outcome.stopped_early is False
    assert sleeps == pytest.approx([10.0], abs=0.01)


def test_productive_throttle_streak_uses_ten_twenty_thirty_backoff() -> None:
    responses = [
        _searxng_response(
            [{"url": "http://example.com/one/"}],
            unresponsive_engines=[["bing", "HTTP error 429"]],
        ),
        _searxng_response(
            [{"url": "http://example.com/two/"}],
            unresponsive_engines=[["google", "Access denied"]],
        ),
        _searxng_response(
            [{"url": "http://example.com/three/"}],
            unresponsive_engines=[["startpage", "CAPTCHA"]],
        ),
        _searxng_response(
            [{"url": "http://example.com/four/"}],
            unresponsive_engines=[["brave", "too many requests"]],
        ),
        _searxng_response([]),
    ]
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=responses):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert len(outcome.rows) == 4
    assert sleeps == pytest.approx([10.0, 20.0, 30.0, 30.0], abs=0.01)
    assert outcome.pacing_delay_seconds == pytest.approx(90.0, abs=0.02)
    assert outcome.throttled_page_count == 4
    assert outcome.hard_retry_count == 0
    assert outcome.stopped_early is False


def test_clean_page_resets_soft_backoff_to_normal_pacing() -> None:
    responses = [
        _searxng_response(
            [{"url": "http://example.com/one/"}],
            unresponsive_engines=[["bing", "too many requests"]],
        ),
        _searxng_response(
            [{"url": "http://example.com/two/"}],
        ),
        _searxng_response([]),
    ]
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=responses):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert sleeps == pytest.approx([10.0, 2.0], abs=0.01)
    assert outcome.pacing_delay_seconds == pytest.approx(12.0, abs=0.02)
    assert outcome.throttled_page_count == 1
    assert outcome.stopped_early is False


def test_empty_throttled_page_uses_short_then_long_retry_ladder() -> None:
    throttled = _searxng_response(
        [],
        unresponsive_engines=[["bing", "HTTP error 429"]],
    )
    sleeps = []
    with patch(
        "urllib.request.urlopen",
        side_effect=[throttled, throttled, throttled],
    ) as mock_open:
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert outcome.rows == []
    assert outcome.stopped_early is True
    assert outcome.hard_retry_count == 2
    assert outcome.hard_retry_delay_seconds == 210
    assert sum(sleeps) == 210
    assert mock_open.call_count == 3
    assert all(
        "pageno=1" in call.args[0]
        for call in mock_open.call_args_list
    )
    assert outcome.error and "stopped early" in outcome.error


def test_hard_retries_are_capped_across_the_entire_run() -> None:
    throttled_empty = _searxng_response(
        [],
        unresponsive_engines=[["bing", "HTTP error 429"]],
    )
    responses = [
        throttled_empty,
        _searxng_response([{"url": "http://example.com/one/"}]),
        throttled_empty,
        _searxng_response([{"url": "http://example.com/two/"}]),
        throttled_empty,
    ]
    sleeps = []
    with patch("urllib.request.urlopen", side_effect=responses) as mock_open:
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert len(outcome.rows) == 2
    assert outcome.hard_retry_count == 2
    assert outcome.hard_retry_delay_seconds == 210
    assert outcome.stopped_early is True
    assert mock_open.call_count == 5
    assert sum(sleeps) == pytest.approx(214.0)


def test_run_dork_search_retry_exhaustion_keeps_partial_rows(
    tmp_path: Path,
) -> None:
    throttled = _searxng_response(
        [],
        unresponsive_engines=[["bing", "too many requests"]],
    )
    responses = [
        _searxng_response([{"url": "http://example.com/one/"}]),
        throttled,
        throttled,
        throttled,
    ]
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch("urllib.request.urlopen", side_effect=responses):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                result = run_dork_search(
                    _options(max_results=10),
                    db_path=tmp_path / "se_dork.db",
                )

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 1
    assert result.deduped_count == 1
    assert result.hard_retry_count == 2
    assert result.stopped_early is True
    assert result.error is None
    assert result.fetch_warning and "stopped early" in result.fetch_warning


def test_fetch_results_http_429_honors_bounded_retry_after() -> None:
    http_error = urllib.error.HTTPError(
        url="http://searxng.local/search",
        code=429,
        msg="Too Many Requests",
        hdrs={"Retry-After": "999"},
        fp=io.BytesIO(b""),
    )
    sleeps = []
    with patch(
        "urllib.request.urlopen",
        side_effect=[http_error, _searxng_response([])],
    ):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert outcome.hard_retry_count == 1
    assert outcome.hard_retry_delay_seconds == 300
    assert sum(sleeps) == 300
    assert outcome.warning and "recovered" in outcome.warning


def test_http_429_without_retry_after_uses_fixed_retry_ladder() -> None:
    def _http_429():
        return urllib.error.HTTPError(
            url="http://searxng.local/search",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b""),
        )

    sleeps = []
    with patch(
        "urllib.request.urlopen",
        side_effect=[_http_429(), _http_429(), _http_429()],
    ):
        outcome = _fetch_results(
            "http://searxng.local",
            "index of",
            10,
            sleep_fn=sleeps.append,
        )

    assert outcome.hard_retry_count == 2
    assert outcome.hard_retry_delay_seconds == 210
    assert sum(sleeps) == 210
    assert outcome.stopped_early is True
    assert outcome.error and "stopped early" in outcome.error


def test_hard_retry_wait_reports_every_thirty_seconds() -> None:
    from experimental.se_dork.service import _wait_for_cooldown

    sleeps = []
    messages = []
    _wait_for_cooldown(
        65,
        retry_number=1,
        progress_cb=messages.append,
        sleep_fn=sleeps.append,
    )

    assert sleeps == [30, 30, 5]
    assert messages == [
        "Upstream retry 1/2: waiting 65s...",
        "Upstream retry 1/2: 35s remaining...",
        "Upstream retry 1/2: 5s remaining...",
    ]


def test_run_dork_search_retry_exhaustion_without_rows_is_error(
    tmp_path: Path,
) -> None:
    throttled = _searxng_response(
        [],
        unresponsive_engines=[["bing", "HTTP error 429"]],
    )
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch(
            "urllib.request.urlopen",
            side_effect=[throttled, throttled, throttled],
        ):
            result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.hard_retry_count == 2
    assert result.hard_retry_delay_seconds == 210
    assert result.stopped_early is True
    assert result.error and "stopped early" in result.error


# ---------------------------------------------------------------------------
# C9: Probe integration (bulk + retained-row scope)
# ---------------------------------------------------------------------------


def test_run_dork_search_bulk_probe_enabled_updates_summary_and_row_state(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.probe.probe_url",
                        side_effect=[
                            _probe_outcome(
                                status="clean",
                                matches=0,
                                preview="pub",
                                snapshot={"run_at": "2026-01-01T00:10:00", "shares": [{"share": "http_root"}]},
                            ),
                            _probe_outcome(
                                status="issue",
                                matches=2,
                                preview="decrypt,notes",
                                snapshot={"run_at": "2026-01-01T00:11:00", "shares": [{"share": "http_root"}]},
                            ),
                        ],
                    ):
                        result = run_dork_search(
                            _options(max_results=10, bulk_probe_enabled=True),
                            db_path=db,
                        )

    assert result.status == RUN_STATUS_DONE
    assert result.probe_enabled is True
    assert result.probe_total == 2
    assert result.probe_clean == 1
    assert result.probe_issue == 1
    assert result.probe_unprobed == 0

    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT probe_status, probe_indicator_matches, probe_preview, probe_snapshot_json FROM dork_results ORDER BY result_id"
        ).fetchall()
    assert sorted(row[:3] for row in rows) == sorted([("clean", 0, "pub"), ("issue", 2, "decrypt,notes")])
    assert all(row[3] and '"shares"' in row[3] for row in rows)


def test_run_dork_search_bulk_probe_targets_retained_rows_only(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                side_effect=[_open_index_result(), _noise_result(), _open_index_result()],
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.probe.probe_url",
                        return_value=_probe_outcome(status="clean", preview="pub"),
                    ) as mock_probe:
                        result = run_dork_search(
                            _options(max_results=10, bulk_probe_enabled=True),
                            db_path=db,
                        )

    assert result.status == RUN_STATUS_DONE
    assert result.deduped_count == 2
    assert result.probe_total == 2
    assert mock_probe.call_count == 2


def test_next_page_waits_for_current_page_probe(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    events = []
    responses = [
        _searxng_response(_results_rows(1)),
        _searxng_response([]),
    ]

    def _urlopen(*args, **kwargs):
        events.append(f"fetch:{len([e for e in events if e.startswith('fetch')]) + 1}")
        return responses.pop(0)

    def _classify(*args, **kwargs):
        events.append("classify")
        return _open_index_result()

    def _probe(*args, **kwargs):
        events.append("probe")
        return _probe_outcome(status="clean")

    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                side_effect=_classify,
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.probe.probe_url",
                        side_effect=_probe,
                    ):
                        result = run_dork_search(
                            _options(
                                max_results=10,
                                bulk_probe_enabled=True,
                            ),
                            db_path=db,
                        )

    assert result.status == RUN_STATUS_DONE
    assert events == ["fetch:1", "classify", "probe", "fetch:2"]


def test_multi_page_counts_match_persisted_run_state(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    responses = [
        _searxng_response(_results_rows(2)),
        _searxng_response([{"url": "http://example.com/final/"}]),
        _searxng_response([]),
    ]
    with patch(
        "experimental.se_dork.service.run_reachability_check",
        return_value=_ok_preflight(),
    ):
        with patch("urllib.request.urlopen", side_effect=responses):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                side_effect=[
                    _open_index_result(),
                    _noise_result(),
                    _open_index_result(),
                ],
            ):
                result = run_dork_search(
                    _options(max_results=10),
                    db_path=db,
                )

    assert result.status == RUN_STATUS_DONE
    assert result.fetched_count == 3
    assert result.verified_count == 3
    assert result.deduped_count == 2
    with sqlite3.connect(str(db)) as conn:
        run_counts = conn.execute(
            """
            SELECT fetched_count, verified_count, deduped_count
              FROM dork_runs
             WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchone()
        retained = conn.execute(
            "SELECT COUNT(*) FROM dork_results WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()[0]
    assert run_counts == (3, 3, 2)
    assert retained == 2


def test_run_dork_search_bulk_probe_failure_keeps_run_done_and_marks_unprobed(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(1)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.probe.probe_url",
                        return_value=_probe_outcome(
                            status="unprobed",
                            matches=0,
                            preview=None,
                            error="timeout",
                        ),
                    ):
                        result = run_dork_search(
                            _options(max_results=10, bulk_probe_enabled=True),
                            db_path=db,
                        )

    assert result.status == RUN_STATUS_DONE
    assert result.probe_total == 1
    assert result.probe_unprobed == 1

    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT probe_status, probe_error FROM dork_results"
        ).fetchone()
    assert row == ("unprobed", "timeout")


def test_run_dork_search_bulk_probe_passes_configured_worker_count(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(1)

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.service._probe_page_rows",
                        return_value=(
                            [],
                            {"total": 0, "clean": 0, "issue": 0, "unprobed": 0},
                        ),
                    ) as mock_probe:
                        result = run_dork_search(
                            _options(
                                max_results=10,
                                bulk_probe_enabled=True,
                                probe_worker_count=7,
                            ),
                            db_path=db,
                        )

    assert result.status == RUN_STATUS_DONE
    assert mock_probe.call_count == 1
    assert mock_probe.call_args.kwargs["worker_count"] == 7


def test_run_dork_search_bulk_probe_invalid_worker_count_falls_back_to_default(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(1)
    opts = SimpleNamespace(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results=10,
        bulk_probe_enabled=True,
        probe_config_path=None,
        probe_worker_count="bad",
    )

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.classifier.classify_url",
                return_value=_open_index_result(),
            ):
                with patch(
                    "experimental.se_dork.probe.build_indicator_patterns",
                    return_value=[],
                ):
                    with patch(
                        "experimental.se_dork.service._probe_page_rows",
                        return_value=(
                            [],
                            {"total": 0, "clean": 0, "issue": 0, "unprobed": 0},
                        ),
                    ) as mock_probe:
                        result = run_dork_search(opts, db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert mock_probe.call_count == 1
    assert mock_probe.call_args.kwargs["worker_count"] == 3


# ---------------------------------------------------------------------------
# QA hardening: never-raise contract
# ---------------------------------------------------------------------------


def test_run_dork_search_reachability_exception_returns_structured_error(
    tmp_path: Path,
) -> None:
    """Unexpected reachability errors must return a structured RunResult."""
    with patch("experimental.se_dork.service.run_reachability_check", side_effect=RuntimeError("boom")):
        result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Reachability error" in result.error
    assert not (tmp_path / "se_dork.db").exists()


def test_run_dork_search_non_int_max_results_returns_structured_result_not_raise(tmp_path: Path) -> None:
    """Non-int options.max_results must not raise; it uses the current default."""
    from experimental.se_dork.models import RunOptions as _RO

    # Bypass the RunOptions type annotation with a plain namespace
    from types import SimpleNamespace
    opts = SimpleNamespace(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results="not_an_int",
    )

    with patch("experimental.se_dork.service.run_reachability_check", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[_searxng_response([])]):
            result = run_dork_search(opts, db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.run_id is not None
