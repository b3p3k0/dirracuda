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
from experimental.se_dork.service import run_dork_search


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


def _searxng_response(results: list) -> MagicMock:
    """Return a mock urlopen context manager yielding SearXNG JSON."""
    body = json.dumps({"results": results}).encode()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
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
):
    from experimental.se_dork.probe import ProbeOutcome

    return ProbeOutcome(
        probe_status=status,
        probe_indicator_matches=matches,
        probe_preview=preview,
        probe_checked_at=checked_at,
        probe_error=error,
    )


# ---------------------------------------------------------------------------
# Preflight failure
# ---------------------------------------------------------------------------


def test_run_dork_search_preflight_fail(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_preflight", return_value=_fail_preflight()):
        result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Preflight failed" in result.error
    # sidecar DB must not have been created
    assert not (tmp_path / "se_dork.db").exists()


# ---------------------------------------------------------------------------
# DB setup failure
# ---------------------------------------------------------------------------


def test_run_dork_search_db_setup_failure(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("experimental.se_dork.service.init_db", side_effect=RuntimeError("disk full")):
            result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "DB setup failed" in result.error


# ---------------------------------------------------------------------------
# Network error after preflight
# ---------------------------------------------------------------------------


def test_run_dork_search_network_error(tmp_path: Path) -> None:
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is not None  # run row was committed before network I/O
    assert result.fetched_count == 0


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_run_dork_search_success(tmp_path: Path) -> None:
    rows = _results_rows(5)
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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


def test_run_dork_search_success_persists_to_db(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    assert result.fetched_count == 2
    assert result.deduped_count == 1


# ---------------------------------------------------------------------------
# max_results cap
# ---------------------------------------------------------------------------


def test_run_dork_search_respects_max_results(tmp_path: Path) -> None:
    rows = _results_rows(5)
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    """After a successful run, _classify_run_results is called and verified_count set."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.service._classify_run_results",
                return_value=3,
            ) as mock_classify:
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.verified_count == 3
    mock_classify.assert_called_once_with(result.run_id, db)


def test_run_dork_search_updates_verified_count(tmp_path: Path) -> None:
    """verified_count in DB is updated by classification phase."""
    from experimental.se_dork.classifier import VERDICT_OPEN_INDEX
    from experimental.se_dork.classifier import ClassifyResult

    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    fake_result = ClassifyResult(
        verdict=VERDICT_OPEN_INDEX, reason_code=None, http_status=200
    )

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
    """If _classify_run_results raises, the run still completes as DONE."""
    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response(rows),
            _searxng_response([]),
        ]):
            with patch(
                "experimental.se_dork.service._classify_run_results",
                side_effect=RuntimeError("classify boom"),
            ):
                result = run_dork_search(_options(max_results=10), db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert result.verified_count == 0


def test_run_dork_search_commit1_exception_returns_structured_error(tmp_path: Path) -> None:
    """If insert_run raises, run_dork_search returns ERROR with run_id=None."""
    from experimental.se_dork.store import insert_run as real_insert_run

    db = tmp_path / "se_dork.db"

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch(
            "experimental.se_dork.service.insert_run",
            side_effect=RuntimeError("disk full"),
        ):
            result = run_dork_search(_options(), db_path=db)

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Run insert failed" in result.error


def test_run_dork_search_persists_clamped_max_results(tmp_path: Path) -> None:
    """dork_runs.max_results must store the clamped value, not raw user input."""
    db = tmp_path / "se_dork.db"

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[
            _searxng_response([]),
        ]):
            result = run_dork_search(_options(max_results=9999), db_path=db)

    assert result.status == RUN_STATUS_DONE
    with sqlite3.connect(str(db)) as conn:
        persisted = conn.execute(
            "SELECT max_results FROM dork_runs WHERE run_id=?", (result.run_id,)
        ).fetchone()[0]
    assert persisted == 500


# ---------------------------------------------------------------------------
# C9: Probe integration (bulk + retained-row scope)
# ---------------------------------------------------------------------------


def test_run_dork_search_bulk_probe_enabled_updates_summary_and_row_state(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(2)

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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
                            _probe_outcome(status="clean", matches=0, preview="pub"),
                            _probe_outcome(status="issue", matches=2, preview="decrypt,notes"),
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
            "SELECT probe_status, probe_indicator_matches, probe_preview FROM dork_results ORDER BY result_id"
        ).fetchall()
    assert sorted(rows) == sorted([("clean", 0, "pub"), ("issue", 2, "decrypt,notes")])


def test_run_dork_search_bulk_probe_targets_retained_rows_only(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(3)

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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


def test_run_dork_search_bulk_probe_failure_keeps_run_done_and_marks_unprobed(tmp_path: Path) -> None:
    db = tmp_path / "se_dork.db"
    rows = _results_rows(1)

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
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

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[_searxng_response([])]):
            with patch(
                "experimental.se_dork.service._probe_run_results",
                return_value={"total": 0, "clean": 0, "issue": 0, "unprobed": 0},
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
    opts = SimpleNamespace(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results=10,
        bulk_probe_enabled=True,
        probe_config_path=None,
        probe_worker_count="bad",
    )

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[_searxng_response([])]):
            with patch(
                "experimental.se_dork.service._probe_run_results",
                return_value={"total": 0, "clean": 0, "issue": 0, "unprobed": 0},
            ) as mock_probe:
                result = run_dork_search(opts, db_path=db)

    assert result.status == RUN_STATUS_DONE
    assert mock_probe.call_count == 1
    assert mock_probe.call_args.kwargs["worker_count"] == 3


# ---------------------------------------------------------------------------
# QA hardening: never-raise contract
# ---------------------------------------------------------------------------


def test_run_dork_search_preflight_exception_returns_structured_error(tmp_path: Path) -> None:
    """If run_preflight raises unexpectedly, run_dork_search must return RunResult(ERROR)."""
    with patch("experimental.se_dork.service.run_preflight", side_effect=RuntimeError("boom")):
        result = run_dork_search(_options(), db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_ERROR
    assert result.run_id is None
    assert "Preflight error" in result.error
    assert not (tmp_path / "se_dork.db").exists()


def test_run_dork_search_non_int_max_results_returns_structured_result_not_raise(tmp_path: Path) -> None:
    """Non-int options.max_results must not raise — falls back to 50 and continues."""
    from experimental.se_dork.models import RunOptions as _RO

    # Bypass the RunOptions type annotation with a plain namespace
    from types import SimpleNamespace
    opts = SimpleNamespace(
        instance_url="http://192.168.1.20:8090",
        query='site:* intitle:"index of /"',
        max_results="not_an_int",
    )

    with patch("experimental.se_dork.service.run_preflight", return_value=_ok_preflight()):
        with patch("urllib.request.urlopen", side_effect=[_searxng_response([])]):
            result = run_dork_search(opts, db_path=tmp_path / "se_dork.db")

    assert result.status == RUN_STATUS_DONE
    assert result.run_id is not None
