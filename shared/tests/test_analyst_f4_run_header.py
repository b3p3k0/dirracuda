"""F4: the report says when it ran, over what, with which rules.

Asked for after a 16-hour run: "I would like to know the start/stop/elapsed
time of the runs". The metadata existed only in the database and the folder
name; nothing the operator opened would tell them.
"""

from __future__ import annotations

import pytest

from experimental.analyst import report_json
from experimental.analyst.report_json import (
    REPORT_SCHEMA_VERSION,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
    elapsed_label,
    run_header_lines,
    throughput_label,
)
from experimental.analyst.report_render import (
    render_html,
    render_markdown,
    render_text,
)

_START = "2026-09-25T15:18:01.170721Z"
_WRITTEN = "2026-09-26T07:01:24.000000Z"


def _run(**overrides) -> dict:
    run = {
        "run_id": "a12d48838f7591e1cc08e663629d097b",
        "report_label": "136.50.251.81 Ted and Sabina read 2",
        "read_mode": "full", "model_tag": "qwen3.8-27b",
        "model_digest": None, "identity_kind": "reported",
        "model_path": None, "model_n_params": None, "model_size_bytes": None,
        "model_ftype": None, "model_n_vocab": None, "model_n_ctx": None,
        "model_n_ctx_train": None, "server_fingerprint": None,
        "created_at_utc": _START, "report_written_at_utc": _WRITTEN,
        "files_read": 932, "files_total": 1244, "flagged_files": 47,
        "source_root": "/home/kevin/testing_docs/136.50.251.81 Ted and Sabina",
        "output_root": "/home/kevin/.dirracuda/data/experimental/out",
        "detector_rules_version": "analyst-detectors-v2",
    }
    run.update(overrides)
    return run


def _report(**overrides) -> dict:
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "run": _run(**overrides),
        "read": {
            "unverified_notice": report_json.UNVERIFIED_NOTICE,
            "host_summary": "h", "likely_owner": None, "contacts": [],
            "risk_level": "LOW", "top_exposures": [],
        },
        "facts": [],
        "affiliations": {
            "organizations": [], "toll_free": [], "toll_free_total": 0,
        },
        "coverage": {
            "discovered": 1244, "terminal": 1244, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }


def _labelled(report: dict) -> dict[str, str]:
    return {label: value for label, value in run_header_lines(report) if label}


# --------------------------------------------------------------------------
# Elapsed time
# --------------------------------------------------------------------------

def test_a_long_run_reads_in_hours_and_minutes():
    assert elapsed_label(_START, _WRITTEN) == "15h 43m"


def test_a_short_run_reads_in_minutes_then_seconds():
    assert elapsed_label(
        "2026-09-25T15:18:00Z", "2026-09-25T15:22:07Z",
    ) == "4m 07s"
    assert elapsed_label(
        "2026-09-25T15:18:00Z", "2026-09-25T15:18:09Z",
    ) == "9s"


@pytest.mark.parametrize(
    ("started", "finished"),
    [
        ("2026-09-25T15:18:00Z", "2026-09-25T15:17:00Z"),  # backwards
        ("not a time", _WRITTEN),
        (_START, None),
        (None, None),
    ],
)
def test_an_elapsed_time_that_cannot_be_computed_is_blank(started, finished):
    assert elapsed_label(started, finished) == ""


# --------------------------------------------------------------------------
# Throughput
# --------------------------------------------------------------------------

def test_throughput_answers_how_long_the_next_run_will_take():
    assert throughput_label(1244, _START, _WRITTEN) == "79 files/hour"


def test_a_fast_run_reads_without_a_decimal():
    assert throughput_label(
        5000, "2026-09-25T15:00:00Z", "2026-09-25T16:00:00Z",
    ) == "5,000 files/hour"


def test_a_slow_run_keeps_one_decimal():
    assert throughput_label(
        3, "2026-09-25T15:00:00Z", "2026-09-25T16:00:00Z",
    ) == "3.0 files/hour"


@pytest.mark.parametrize("files", [0, -1, None, "many"])
def test_throughput_without_a_file_count_is_blank(files):
    assert throughput_label(files, _START, _WRITTEN) == ""


def test_throughput_over_no_measurable_time_is_blank():
    assert throughput_label(
        10, "2026-09-25T15:00:00Z", "2026-09-25T15:00:00.4Z",
    ) == ""


# --------------------------------------------------------------------------
# The header
# --------------------------------------------------------------------------

def test_the_header_answers_when_over_what_and_with_which_rules():
    header = _labelled(_report())
    assert header["Ran"] == (
        "2026-09-25 15:18 → 2026-09-26 07:01 UTC  (15h 43m)  ·  79 files/hour"
    )
    assert header["Source"].endswith("136.50.251.81 Ted and Sabina")
    assert header["Output"].endswith("/out")
    assert header["Model"] == (
        "qwen3.8-27b (reported by the server, not verified)  ·  Full read"
    )
    assert header["Provenance"] == (
        "run a12d4883  ·  analyst-detectors-v2  ·  report schema 5"
    )


def test_times_are_always_utc_and_say_so():
    """Local time is friendlier here and ambiguous the moment it is shared."""
    assert "UTC" in _labelled(_report())["Ran"]


def test_the_counts_line_carries_no_label():
    lines = run_header_lines(_report())
    counts = [value for label, value in lines if not label]
    assert counts == ["1,244 files found  ·  932 read  ·  47 flagged"]


def test_an_unfinished_report_still_shows_when_it_started():
    header = _labelled(_report(report_written_at_utc=""))
    assert header["Ran"] == "2026-09-25 15:18 UTC"


def test_a_report_without_the_new_fields_simply_shows_fewer_lines():
    """A pre-F4 report has no source, no output, no rules version."""
    run = _run()
    for key in (
        "source_root", "output_root", "report_written_at_utc",
        "detector_rules_version",
    ):
        run.pop(key)
    header = _labelled({"report_schema_version": 2, "run": run})
    assert "Source" not in header
    assert "Output" not in header
    assert "analyst-detectors" not in header["Provenance"]
    assert header["Ran"] == "2026-09-25 15:18 UTC"


def test_a_malformed_report_yields_no_header():
    assert run_header_lines({}) == []
    assert run_header_lines({"run": "not an object"}) == []


# --------------------------------------------------------------------------
# The surfaces
# --------------------------------------------------------------------------

def test_every_surface_carries_the_header():
    report = _report()
    for rendered in (render_markdown(report), render_text(report),
                     render_html(report)):
        assert "15h 43m" in rendered
        assert "79 files/hour" in rendered
        assert "analyst-detectors-v2" in rendered
        assert "136.50.251.81 Ted and Sabina" in rendered


def test_the_model_line_left_what_this_is():
    """It is a fact about the run, not part of the model's read."""
    rendered = render_text(_report())
    header, _, read = rendered.partition("WHAT THIS IS")
    assert "qwen3.8-27b" in header
    assert "qwen3.8-27b" not in read


def test_an_operator_supplied_path_cannot_inject_structure():
    """source_root is typed by the operator and reaches markdown and HTML."""
    report = _report(source_root="/tmp/<script>alert(1)</script>_*x*")
    assert "<script>" not in render_html(report)
    assert "&lt;script&gt;" in render_html(report)
    assert "\\*x\\*" in render_markdown(report)


def test_the_schema_version_reads_every_older_one():
    assert REPORT_SCHEMA_VERSION == 5
    assert SUPPORTED_REPORT_SCHEMA_VERSIONS == (1, 2, 3, 4, 5)


# --------------------------------------------------------------------------
# Determinism: the timestamp must survive a crashed finalization
# --------------------------------------------------------------------------

def test_the_report_timestamp_never_comes_from_a_clock():
    """The bug this guards against, found by test_analyst_c12.

    report.json carries when the report was built, and a finalization that
    crashes and resumes has to rebuild it byte for byte -- the manifest digest
    is durable and is compared on resume. A first draft used the wall clock
    and produced a different digest every time. The value now comes from
    analyst_runs.report_built_at_utc, which begin_finalization writes once
    with COALESCE and keeps.
    """
    import inspect

    from experimental.analyst import report_state

    source = inspect.getsource(report_state.build_report_json_payload)
    assert "report_built_at_utc" in inspect.getsource(report_state)
    for forbidden in ("datetime.now", "time.time", "utcnow"):
        assert forbidden not in source


def test_begin_finalization_keeps_the_first_timestamp():
    """COALESCE, so a resumed finalization does not restamp the report."""
    import inspect

    from experimental.analyst import checkpoint

    source = inspect.getsource(checkpoint.begin_finalization)
    assert "COALESCE(report_built_at_utc,?)" in source
