"""A run that found nothing readable must say so, not look like a silent failure.

Found in real use: a source folder holding one 2-byte ``desktop.ini`` ran for
one second, reported "complete", and opened an empty report. Nothing had
failed -- there was nothing to read -- but neither the run row nor the report
said which.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from experimental.analyst.report_json import coverage_note
from experimental.analyst.service import (
    DirectoryRunRequest,
    create_directory_run,
    list_run_summaries,
)
from experimental.analyst.store import open_connection
from shared.path_service import get_paths


def _report(*, files_read=0, discovered=1, **coverage):
    base = {"terminal": discovered, "no_text_layer": 0, "parse_failed": 0,
            "unsupported": 0}
    base.update(coverage)
    base["discovered"] = discovered
    return {"run": {"files_read": files_read}, "coverage": base}


# --------------------------------------------------------------------------
# coverage_note -- pure
# --------------------------------------------------------------------------

def test_a_report_with_content_gets_no_note():
    assert coverage_note(_report(files_read=3)) == ""


def test_the_real_case_names_the_count_and_the_reason():
    """One 2-byte desktop.ini: parsed fine, held no text."""
    assert coverage_note(_report()) == (
        "Nothing was sent to the model. 1 file found and none could be read "
        "(no readable text)."
    )


def test_an_empty_folder_says_so():
    assert coverage_note(_report(discovered=0)) == (
        "No files were found under the source folder."
    )


def test_known_reasons_are_listed_and_zero_ones_are_not():
    note = coverage_note(
        _report(discovered=4, unsupported=3, no_text_layer=1),
    )
    assert "4 files found" in note
    assert "3 unsupported format; 1 no text layer" in note
    assert "parsed" not in note


def test_a_malformed_report_does_not_raise():
    assert coverage_note({}) == ""
    assert coverage_note({"run": {"files_read": "x"}, "coverage": {}}) == ""


# --------------------------------------------------------------------------
# The run row
# --------------------------------------------------------------------------

@pytest.fixture
def completed_run(tmp_path: Path):
    """Create one run and let the caller finish it with a completion code."""
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "one.txt").write_text("public", encoding="utf-8")
    paths = get_paths(home_root=tmp_path / "home")
    run_id, _inventory = create_directory_run(
        DirectoryRunRequest(source, output, "host12", "fast"),
        path=paths.analyst_db_file,
        run_id_factory=lambda _size: "b" * 32,
    )

    def finish(code: str) -> None:
        conn = open_connection(paths.analyst_db_file)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE analyst_runs SET state='complete',completion_code=?,"
                "finished_at_utc=?,finalization_token=? WHERE run_id=?",
                (code, "2026-09-24T12:02:00Z", "c" * 64, run_id),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()

    return paths.analyst_db_file, finish


def test_a_no_content_run_is_labelled_nothing_readable(completed_run):
    db_path, finish = completed_run
    finish("complete_no_supported_content")
    listed = list_run_summaries(path=db_path)
    assert listed[0].completion_code == "complete_no_supported_content"
    assert listed[0].result_label == "nothing readable"


def test_an_ordinary_completion_is_unchanged(completed_run):
    db_path, finish = completed_run
    finish("complete")
    listed = list_run_summaries(path=db_path)
    assert listed[0].completion_code == "complete"
    assert listed[0].result_label == "-"
