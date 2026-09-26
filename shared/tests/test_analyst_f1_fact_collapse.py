"""F1: one fact row per value per file, carrying how often it occurs.

Found in real use. A 1,244-file run filled report.json's 500-fact budget with
242 distinct values -- the rest differed only in `provenance`, which no report
surface has a column for, so they rendered as byte-identical rows. Every slot
went to HIGH-rank detector hits and not one of 1,907 model findings reached the
report.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from experimental.analyst import report_json
from experimental.analyst.report_contract import MAX_REPORT_JSON_FACTS
from experimental.analyst.report_json import (
    GroundedFact,
    REPORT_SCHEMA_VERSION,
    SUPPORTED_REPORT_SCHEMA_VERSIONS,
    fact_rank_label,
    fact_seen_label,
)
from experimental.analyst.report_render import (
    render_facts_csv,
    render_markdown,
    render_text,
)
from experimental.analyst.report_state import _load_ranked_facts
from experimental.analyst.service import DirectoryRunRequest, create_directory_run
from experimental.analyst.store import open_connection
from shared.path_service import get_paths


# --------------------------------------------------------------------------
# The schema
# --------------------------------------------------------------------------

def test_the_report_version_reads_every_older_one():
    assert REPORT_SCHEMA_VERSION == 5
    assert SUPPORTED_REPORT_SCHEMA_VERSIONS == (1, 2, 3, 4, 5)


def test_a_fact_defaults_to_one_occurrence_and_a_valid_verdict():
    """The two new fields default, so callers written before F1 still work."""
    fact = GroundedFact(
        kind="ssn", category="pii", quote="630-15-0629", file="a.pdf",
        provenance="page page-1", rank="HIGH", source="detector",
    )
    assert fact.occurrences == 1
    assert fact.plausibility == "valid"
    assert fact.subject == "unknown"


@pytest.mark.parametrize("bad", [0, -1, "4", True, 1.0])
def test_an_impossible_occurrence_count_is_refused(bad):
    with pytest.raises(report_json.ReportValidationError):
        GroundedFact(
            kind="ssn", category="pii", quote="q", file="a", provenance="p",
            rank="HIGH", source="detector", occurrences=bad,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("plausibility", "probably"), ("plausibility", "public"),
     ("subject", "corporate")],
)
def test_an_unknown_screening_value_is_refused(field, value):
    """`public` moved to the subject axis; plausibility must not take it."""
    with pytest.raises(report_json.ReportValidationError):
        GroundedFact(
            kind="ssn", category="pii", quote="q", file="a", provenance="p",
            rank="HIGH", source="detector", **{field: value},
        )


def test_a_v2_report_validates_without_the_new_fields():
    """An existing report on disk must keep opening."""
    report = _report(version=2)
    for fact in report["facts"]:
        fact.pop("occurrences")
        fact.pop("plausibility")
        fact.pop("subject")
    report_json.validate_report_json(report)


def test_a_v3_report_requires_the_new_fields():
    report = _report()
    report["facts"][0].pop("occurrences")
    with pytest.raises(report_json.ReportValidationError):
        report_json.validate_report_json(report)


# --------------------------------------------------------------------------
# The labels every surface shares
# --------------------------------------------------------------------------

def test_a_single_occurrence_shows_no_count():
    assert fact_seen_label({"occurrences": 1}) == ""
    assert fact_seen_label({}) == ""


def test_repeats_show_a_count():
    assert fact_seen_label({"occurrences": 4}) == "4x"


def test_a_demoted_fact_says_why_it_was_demoted():
    assert fact_rank_label({"rank": "HIGH"}) == "HIGH"
    assert fact_rank_label({"rank": "HIGH", "plausibility": "valid"}) == "HIGH"
    assert fact_rank_label(
        {"rank": "MED", "plausibility": "suspect"}
    ) == "MED · suspect"
    assert fact_rank_label(
        {"rank": "low", "subject": "organizational"}
    ) == "low · organizational"


def test_the_two_screening_axes_are_reported_separately():
    """A real toll-free number is not "suspect"; it is simply not personal."""
    assert fact_rank_label({
        "rank": "low", "plausibility": "suspect", "subject": "organizational",
    }) == "low · suspect · organizational"


# --------------------------------------------------------------------------
# The collapse itself, against a real database
# --------------------------------------------------------------------------

@pytest.fixture
def run(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "one.txt").write_text("public", encoding="utf-8")
    (source / "two.txt").write_text("public", encoding="utf-8")
    paths = get_paths(home_root=tmp_path / "home")
    run_id, _inv = create_directory_run(
        DirectoryRunRequest(source, output, "host12", "fast"),
        path=paths.analyst_db_file,
        run_id_factory=lambda _size: "b" * 32,
    )
    return run_id, paths.analyst_db_file


def _add_hits(db_path: Path, run_id: str, rows) -> None:
    """Write detector hits as (file_ordinal, kind, value, start)."""
    conn = open_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        files = {
            int(r["ordinal"]): r["file_id"]
            for r in conn.execute(
                "SELECT file_id,ordinal FROM analyst_files WHERE run_id=?",
                (run_id,),
            )
        }
        per_file: dict[int, int] = {}
        for ordinal, kind, value, start in rows:
            file_id = files[ordinal]
            hit_ordinal = per_file.get(ordinal, 0)
            per_file[ordinal] = hit_ordinal + 1
            conn.execute(
                "INSERT INTO analyst_detector_hits("
                "file_id,ordinal,kind,value,start_char,end_char) "
                "VALUES(?,?,?,?,?,?)",
                (file_id, hit_ordinal, kind, value, start, start + len(value)),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _facts(db_path: Path, run_id: str):
    conn = open_connection(db_path, read_only=True)
    try:
        return _load_ranked_facts(conn, run_id)
    finally:
        conn.close()


def test_the_same_value_in_one_file_becomes_one_fact(run):
    run_id, db_path = run
    _add_hits(db_path, run_id, [
        (0, "ssn", "630-15-0629", 10),
        (0, "ssn", "630-15-0629", 200),
        (0, "ssn", "630-15-0629", 900),
        (0, "ssn", "630-15-0629", 1500),
    ])
    facts = _facts(db_path, run_id)
    assert len(facts) == 1
    assert facts[0].occurrences == 4
    assert facts[0].quote == "630-15-0629"


def test_the_kept_provenance_is_the_first_occurrence(run):
    run_id, db_path = run
    _add_hits(db_path, run_id, [
        (0, "ssn", "630-15-0629", 10),
        (0, "ssn", "630-15-0629", 5000),
    ])
    facts = _facts(db_path, run_id)
    assert facts[0].provenance == "characters 10-21"


def test_the_same_value_in_two_files_stays_two_facts(run):
    """The File column has to keep meaning what it says."""
    run_id, db_path = run
    _add_hits(db_path, run_id, [
        (0, "ssn", "630-15-0629", 10),
        (0, "ssn", "630-15-0629", 20),
        (1, "ssn", "630-15-0629", 30),
    ])
    facts = _facts(db_path, run_id)
    assert len(facts) == 2
    assert sorted(fact.occurrences for fact in facts) == [1, 2]
    assert len({fact.file for fact in facts}) == 2


def test_different_values_are_never_merged(run):
    run_id, db_path = run
    _add_hits(db_path, run_id, [
        (0, "ssn", "630-15-0629", 10),
        (0, "ssn", "636-03-5272", 20),
    ])
    facts = _facts(db_path, run_id)
    assert len(facts) == 2
    assert all(fact.occurrences == 1 for fact in facts)


def test_the_collapse_happens_before_the_cap(run):
    """The bug that mattered: duplicates were spending the fact budget."""
    run_id, db_path = run
    repeats = MAX_REPORT_JSON_FACTS + 200
    _add_hits(
        db_path, run_id,
        [(0, "ssn", "630-15-0629", index * 20) for index in range(repeats)]
        + [(1, "routing", "314074269", 10)],
    )
    facts = _facts(db_path, run_id)
    assert len(facts) == 2, "the repeats consumed the budget again"
    seen = {fact.kind: fact.occurrences for fact in facts}
    assert seen == {"ssn": repeats, "routing": 1}


# --------------------------------------------------------------------------
# The surfaces
# --------------------------------------------------------------------------

def _report(*, version: int = 4) -> dict:
    def fact(quote: str, occurrences: int) -> dict:
        return {
            "kind": "ssn", "category": "pii", "quote": quote,
            "file": "Sabina/Fed loan/IncomeDrivenRepayment_2018.pdf",
            "provenance": "page page-1", "rank": "HIGH", "source": "detector",
            "occurrences": occurrences, "plausibility": "valid",
            "subject": "unknown",
        }

    run = {
        "run_id": "r" * 32, "report_label": "L", "read_mode": "quick",
        "model_tag": "m", "model_digest": None,
        "created_at_utc": "2026-09-25T12:00:00Z",
        "files_read": 1, "files_total": 1, "flagged_files": 1,
    }
    if version >= 2:
        run |= {
            "identity_kind": "reported", "model_path": None,
            "model_n_params": None, "model_size_bytes": None,
            "model_ftype": None, "model_n_vocab": None, "model_n_ctx": None,
            "model_n_ctx_train": None, "server_fingerprint": None,
        }
    report = {
        "report_schema_version": version,
        "run": run,
        "read": {
            "unverified_notice": report_json.UNVERIFIED_NOTICE,
            "host_summary": "h", "likely_owner": None, "contacts": [],
            "risk_level": "HIGH", "top_exposures": [],
        },
        "facts": [fact("630-15-0629", 4), fact("636-03-5272", 1)],
        "coverage": {
            "discovered": 1, "terminal": 1, "no_text_layer": 0,
            "parse_failed": 0, "unsupported": 0,
        },
    }
    if version >= 4:
        report["affiliations"] = {
            "organizations": [
                {"domain": "utsa.edu", "files": 19, "occurrences": 36},
            ],
            "toll_free": [],
            "toll_free_total": 0,
        }
    return report


def test_csv_carries_the_count():
    rows = list(csv.reader(io.StringIO(render_facts_csv(_report()))))
    assert rows[0] == ["Kind", "Value", "File", "Seen", "Rank"]
    assert rows[1][3] == "4x"
    assert rows[2][3] == ""


def test_markdown_and_text_carry_the_count():
    assert "| 4x |" in render_markdown(_report())
    assert "| 4x |" in render_text(_report())
