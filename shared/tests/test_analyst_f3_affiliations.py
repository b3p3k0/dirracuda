"""F3: which organisations this host's documents keep referring to.

Derived, not prompted. The signal is how many DISTINCT FILES mention a domain:
a product manual in a downloads folder names its vendor several times and that
is not a relationship. In one real 1,244-file corpus, 63 of 77 candidate
domains appeared in exactly one file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from experimental.analyst import report_json
from experimental.analyst.report_json import (
    Affiliation,
    FREE_MAIL_DOMAINS,
    MAX_AFFILIATIONS,
    MAX_TOLL_FREE,
    MIN_AFFILIATION_FILES,
    TollFreeContact,
    affiliation_lines,
    email_domain,
    is_organizational_domain,
)
from experimental.analyst.report_render import (
    render_html,
    render_markdown,
    render_text,
)
from experimental.analyst.report_state import _load_affiliations
from experimental.analyst.service import DirectoryRunRequest, create_directory_run
from experimental.analyst.store import open_connection
from shared.path_service import get_paths


# --------------------------------------------------------------------------
# The mailbox-provider distinction
# --------------------------------------------------------------------------

def test_a_company_domain_is_an_affiliation():
    assert is_organizational_domain("utsa.edu")
    assert is_organizational_domain("dfps.state.tx.us")


@pytest.mark.parametrize(
    "domain", ["gmail.com", "hotmail.com", "yahoo.com", "outlook.com", "me.com"],
)
def test_a_mailbox_provider_is_not_an_affiliation(domain):
    """"@hotmail.com" says someone has a Hotmail account, nothing more.

    Without this, hotmail.com topped one real corpus with 83 files and buried
    every genuine affiliation beneath it.
    """
    assert domain in FREE_MAIL_DOMAINS
    assert not is_organizational_domain(domain)


def test_the_domain_is_read_case_insensitively():
    assert email_domain("Person@UTSA.edu") == "utsa.edu"
    assert email_domain("not-an-address") == ""
    assert not is_organizational_domain("")


# --------------------------------------------------------------------------
# The contracts
# --------------------------------------------------------------------------

def test_an_affiliation_cannot_have_fewer_mentions_than_files():
    with pytest.raises(report_json.ReportValidationError):
        Affiliation(domain="utsa.edu", files=4, occurrences=2)


@pytest.mark.parametrize("files", [0, -1])
def test_an_affiliation_needs_at_least_one_file(files):
    with pytest.raises(report_json.ReportValidationError):
        Affiliation(domain="utsa.edu", files=files, occurrences=9)


def test_a_toll_free_contact_carries_where_it_was_found():
    contact = TollFreeContact(
        value="800-772-1213", files=5, example_file="Sabina/CSN/4506t.pdf",
    )
    assert contact.example_file == "Sabina/CSN/4506t.pdf"


# --------------------------------------------------------------------------
# The rollup, against a real database
# --------------------------------------------------------------------------

@pytest.fixture
def run(tmp_path: Path):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    for index in range(6):
        (source / f"{index}.txt").write_text("public", encoding="utf-8")
    paths = get_paths(home_root=tmp_path / "home")
    run_id, _inv = create_directory_run(
        DirectoryRunRequest(source, output, "host12", "fast"),
        path=paths.analyst_db_file,
        run_id_factory=lambda _size: "b" * 32,
    )
    return run_id, paths.analyst_db_file


def _add(db_path: Path, run_id: str, rows) -> None:
    """Write hits as (file_ordinal, kind, value)."""
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
        start = 0
        for ordinal, kind, value in rows:
            hit_ordinal = per_file.get(ordinal, 0)
            per_file[ordinal] = hit_ordinal + 1
            start += 40
            conn.execute(
                "INSERT INTO analyst_detector_hits("
                "file_id,ordinal,kind,value,start_char,end_char) "
                "VALUES(?,?,?,?,?,?)",
                (files[ordinal], hit_ordinal, kind, value, start,
                 start + len(value)),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _affiliations(db_path: Path, run_id: str):
    conn = open_connection(db_path, read_only=True)
    try:
        return _load_affiliations(conn, run_id)
    finally:
        conn.close()


def test_a_domain_in_three_files_is_a_pattern(run):
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "email", "a@utsa.edu"),
        (1, "email", "b@utsa.edu"),
        (2, "email", "c@utsa.edu"),
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert [item.domain for item in organizations] == ["utsa.edu"]
    assert organizations[0].files == 3


def test_a_domain_in_two_files_is_a_coincidence(run):
    """The threshold the HI set: two is a coincidence, three is a pattern."""
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "email", "a@utsa.edu"),
        (1, "email", "b@utsa.edu"),
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert organizations == ()
    assert MIN_AFFILIATION_FILES == 3


def test_many_mentions_in_one_file_are_not_a_relationship(run):
    """The product-manual case. Repetition inside one document proves nothing."""
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "email", "wiring@seymourduncan.com") for _ in range(9)
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert organizations == ()


def test_a_mailbox_provider_never_reaches_the_rollup(run):
    run_id, db_path = run
    _add(db_path, run_id, [
        (index, "email", f"person{index}@hotmail.com") for index in range(5)
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert organizations == ()


def test_different_people_at_one_organisation_count_once(run):
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "email", "harriett.romo@utsa.edu"),
        (1, "email", "disability.services@utsa.edu"),
        (2, "email", "someone.else@utsa.edu"),
        (3, "email", "harriett.romo@utsa.edu"),
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert len(organizations) == 1
    assert organizations[0].files == 4
    assert organizations[0].occurrences == 4


def test_organisations_rank_by_distinct_files(run):
    run_id, db_path = run
    _add(db_path, run_id, [
        *[(index, "email", "a@neisd.net") for index in range(3)],
        *[(index, "email", "b@utsa.edu") for index in range(5)],
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert [item.domain for item in organizations] == ["utsa.edu", "neisd.net"]


def test_a_reserved_documentation_domain_is_not_an_affiliation(run):
    """example.com screens as suspect, so it is evidence but not a relationship."""
    run_id, db_path = run
    _add(db_path, run_id, [
        (index, "email", f"person{index}@example.com") for index in range(4)
    ])
    organizations, _numbers, _total = _affiliations(db_path, run_id)
    assert organizations == ()


# --------------------------------------------------------------------------
# Toll-free: collected, not analysed
# --------------------------------------------------------------------------

def test_toll_free_numbers_are_collected_with_their_source(run):
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "phone", "800-772-1213"),
        (1, "phone", "800-772-1213"),
        (2, "phone", "855-889-4325"),
    ])
    _orgs, numbers, total = _affiliations(db_path, run_id)
    assert total == 2
    assert numbers[0].value == "800-772-1213"
    assert numbers[0].files == 2
    assert numbers[0].example_file.endswith(".txt")


def test_an_ordinary_number_is_not_collected_as_toll_free(run):
    run_id, db_path = run
    _add(db_path, run_id, [(0, "phone", "210-234-5678")])
    _orgs, numbers, total = _affiliations(db_path, run_id)
    assert numbers == ()
    assert total == 0


def test_the_toll_free_list_is_capped_but_the_total_is_honest(run):
    """A real corpus held 159 distinct numbers. The count must not lie."""
    run_id, db_path = run
    _add(db_path, run_id, [
        (0, "phone", f"800-234-{5000 + index:04d}")
        for index in range(MAX_TOLL_FREE + 12)
    ])
    _orgs, numbers, total = _affiliations(db_path, run_id)
    assert len(numbers) == MAX_TOLL_FREE
    assert total == MAX_TOLL_FREE + 12


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _report(organizations, numbers=(), total=0) -> dict:
    from experimental.analyst.report_json import (
        Coverage,
        HostRead,
        RunMeta,
        build_report_json,
    )

    run = RunMeta(
        run_id="r" * 32, report_label="136.50.251.81", read_mode="full",
        model_tag="qwen3.8-27b", model_digest=None, identity_kind="reported",
        created_at_utc="2026-09-25T12:00:00Z",
        files_read=6, files_total=6, flagged_files=0,
    )
    read = HostRead(
        host_summary="h", likely_owner=None, contacts=(), risk_level="LOW",
        top_exposures=(),
    )
    return build_report_json(
        run, read, (), Coverage(6, 6, 0, 0, 0),
        affiliations=organizations, toll_free=numbers, toll_free_total=total,
    )


def test_every_surface_shows_an_organisation():
    report = _report((Affiliation("utsa.edu", 19, 36),))
    assert "utsa.edu" in render_markdown(report)
    assert "utsa.edu" in render_text(report)
    assert "utsa.edu" in render_html(report)
    assert "utsa.edu" in affiliation_lines(report)


def test_toll_free_is_labelled_as_not_analysed():
    report = _report(
        (), (TollFreeContact("800-772-1213", 5, "Sabina/CSN/4506t.pdf"),), 159,
    )
    for rendered in (render_markdown(report), render_text(report),
                     render_html(report)):
        assert "collected, not analysed" in rendered
        assert "159" in rendered
    assert "159 toll-free" in affiliation_lines(report)


def test_an_empty_block_says_so_rather_than_looking_broken():
    report = _report(())
    assert "appears in enough files" in render_text(report)
    assert "appears in enough files" in affiliation_lines(report)


def test_a_pre_v4_report_renders_without_the_block():
    """A report written before this card has no block and must still open."""
    report = _report((Affiliation("utsa.edu", 19, 36),))
    report.pop("affiliations")
    for key in (
        "source_root", "output_root", "report_written_at_utc",
        "detector_rules_version",
    ):
        report["run"].pop(key)
    report["report_schema_version"] = 3
    report_json.validate_report_json(report)
    assert affiliation_lines(report) == "(none)"
    assert "AFFILIATIONS" in render_text(report)


def test_the_block_is_refused_when_it_is_not_canonical():
    with pytest.raises(report_json.ReportValidationError):
        report_json.validate_report_json(
            _report((Affiliation("a.edu", 3, 3), Affiliation("b.edu", 9, 9)))
        )


def test_a_mailbox_provider_is_refused_at_the_contract_boundary():
    with pytest.raises(report_json.ReportValidationError):
        report_json.validate_report_json(
            _report((Affiliation("gmail.com", 9, 9),))
        )


def test_an_under_threshold_affiliation_is_refused():
    with pytest.raises(report_json.ReportValidationError):
        report_json.validate_report_json(
            _report((Affiliation("utsa.edu", 2, 2),))
        )


def test_the_affiliation_list_is_bounded():
    assert MAX_AFFILIATIONS == 20
