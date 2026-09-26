"""
Generated adversarial containers must be rejected by the pre-parse gates.

Every case is built under tmp_path at test time and bounded. Nothing malicious
is committed to the repository.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from shared.tests import analyst_container_cases as cases


def test_xxe_docx_is_detected_before_any_xml_parse(tmp_path: Path) -> None:
    p = cases.make_xxe_docx(tmp_path)
    accepted, reason = cases.container_gate(p)
    assert accepted, reason          # structurally a fine zip
    with zipfile.ZipFile(p) as zf:
        data = zf.read("word/document.xml")
    assert cases.xml_declares_external_entity(data), (
        "external entity declaration must be visible before parsing")


def test_zip_bomb_rejected_on_ratio_or_size(tmp_path: Path) -> None:
    p = cases.make_zip_bomb(tmp_path)
    accepted, reason = cases.container_gate(p)
    assert not accepted
    assert reason in ("expanded_size_exceeded", "compression_ratio_exceeded")


def test_zip_bomb_stays_inside_its_bound(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        cases.make_zip_bomb(tmp_path, expanded=cases.MAX_BUILD_EXPANDED_BYTES + 1)


def test_extreme_member_count_rejected(tmp_path: Path) -> None:
    p = cases.make_many_members(tmp_path)
    accepted, reason = cases.container_gate(p)
    assert not accepted
    assert reason in ("member_count_exceeded", "compression_ratio_exceeded")


def test_member_count_builder_is_bounded(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        cases.make_many_members(tmp_path, members=cases.MAX_BUILD_MEMBERS + 1)


def test_path_traversal_member_rejected(tmp_path: Path) -> None:
    p = cases.make_path_traversal(tmp_path)
    accepted, reason = cases.container_gate(p)
    assert not accepted
    assert reason == "path_traversal_member"


def test_deep_nesting_container_is_accepted_but_xml_is_the_risk(
        tmp_path: Path) -> None:
    p = cases.make_deep_nesting(tmp_path)
    accepted, _reason = cases.container_gate(p)
    assert accepted, "nesting is an XML-parser risk, not a container-gate one"


def test_extension_is_never_authority(tmp_path: Path) -> None:
    """A zip named .pdf must sniff as a zip, so it never reaches a PDF parser."""
    p = cases.make_zip_named_pdf(tmp_path)
    assert p.suffix == ".pdf"
    assert cases.sniff_magic(p) == "zip"


def test_traversal_builder_does_not_escape_tmp_path(tmp_path: Path) -> None:
    """Building the case must not itself write outside tmp_path."""
    cases.make_path_traversal(tmp_path)
    assert not Path("/tmp/dirracuda_escape.txt").exists()
