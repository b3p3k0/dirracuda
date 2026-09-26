"""
Gold-set integrity, de-identification, and class balance.

The gold set is the only place precision/recall can come from, so its labels and
its provenance have to be verifiable without trusting the generator.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "shared" / "tests" / "fixtures" / "analyst_gold"
MANIFEST = GOLD / "manifest.json"

EXPECTED_TOTAL = 166
EXPECTED_STRATA = {
    "positive_control": 80,
    "negative_clean": 20,
    "negative_near_miss": 20,
    "injection": 8,
    "injection_clean_twin": 8,
    "boundary": 24,
    "output_truncation": 3,
    "input_truncation": 3,
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_declares_exact_count(manifest: dict) -> None:
    assert manifest["document_count"] == EXPECTED_TOTAL
    assert len(manifest["documents"]) == EXPECTED_TOTAL


def test_manifest_stays_within_file_size_guardrail() -> None:
    assert len(MANIFEST.read_text(encoding="utf-8").splitlines()) <= 1700


def test_generated_documents_have_no_trailing_whitespace() -> None:
    for path in sorted((GOLD / "docs").glob("*.txt")):
        for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            assert line == line.rstrip(), f"{path.name}:{line_number}"


def test_every_fixture_exists_and_hashes_match(manifest: dict) -> None:
    for row in manifest["documents"]:
        p = GOLD / row["path"]
        assert p.is_file(), f"missing {row['path']}"
        got = hashlib.sha256(p.read_bytes()).hexdigest()
        assert got == row["sha256"], f"{row['doc_id']} content drifted"
        assert p.stat().st_size == row["size"]


def test_class_balance(manifest: dict) -> None:
    counts = Counter(r["stratum"] for r in manifest["documents"])
    assert dict(counts) == EXPECTED_STRATA


def test_positive_controls_are_balanced_across_categories(manifest: dict) -> None:
    per = Counter()
    for row in manifest["documents"]:
        if row["stratum"] == "positive_control":
            for cat in row["categories_present"]:
                per[cat] += 1
    assert dict(per) == {"pii": 20, "financial": 20,
                         "contact": 20, "demographic": 20}


def test_every_injection_has_a_clean_twin(manifest: dict) -> None:
    by_id = {r["doc_id"]: r for r in manifest["documents"]}
    injections = [r for r in manifest["documents"] if r["stratum"] == "injection"]
    assert len(injections) == 8
    for row in injections:
        twin_id = row["clean_twin_id"]
        assert twin_id, f"{row['doc_id']} has no twin"
        twin = by_id[twin_id]
        assert twin["stratum"] == "injection_clean_twin"
        # identical sensitive content: the twin differs only by the payload
        assert twin["categories_present"] == row["categories_present"]
        assert twin["expected_identifiers"] == row["expected_identifiers"]


def test_injection_differs_from_twin_only_by_added_lines(manifest: dict) -> None:
    by_id = {r["doc_id"]: r for r in manifest["documents"]}
    for row in manifest["documents"]:
        if row["stratum"] != "injection":
            continue
        inj = (GOLD / row["path"]).read_text(encoding="utf-8").splitlines()
        twin = (GOLD / by_id[row["clean_twin_id"]]["path"]).read_text(
            encoding="utf-8").splitlines()
        assert set(twin).issubset(set(inj)), (
            f"{row['doc_id']} removed content from its twin; the pair must "
            "differ only by the injected payload")
        assert len(inj) > len(twin)


def test_boundary_documents_straddle_the_reference_chunk(manifest: dict) -> None:
    ch = manifest["chunk_chars_reference"]
    rows = [r for r in manifest["documents"] if r["stratum"] == "boundary"]
    assert len(rows) == 24
    for row in rows:
        text = (GOLD / row["path"]).read_text(encoding="utf-8")
        ident = row["expected_identifiers"][0]
        start = text.index(ident)
        assert start < ch < start + len(ident), (
            f"{row['doc_id']} identifier does not cross the chunk cut")


def test_input_truncation_docs_are_flagged_as_context_rule_exceptions(
        manifest: dict) -> None:
    rows = [r for r in manifest["documents"]
            if r["stratum"] == "input_truncation"]
    assert rows and all(r["context_rule_exception"] for r in rows)
    # and no other stratum claims the exception
    others = [r for r in manifest["documents"]
              if r["stratum"] != "input_truncation" and r["context_rule_exception"]]
    assert not others


def test_output_truncation_docs_carry_many_findings(manifest: dict) -> None:
    rows = [r for r in manifest["documents"]
            if r["stratum"] == "output_truncation"]
    assert rows
    for row in rows:
        assert len(row["expected_identifiers"]) >= 40, (
            "output truncation needs enough expected findings to exceed "
            "num_predict; a merely long input tests the wrong boundary")


# ---------------------------------------------------------------------------
# De-identification
# ---------------------------------------------------------------------------
_SSN = re.compile(r"\b(\d{3})-\d{2}-\d{4}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"\b\d{3}-(\d{4})\b")

ALLOWED_DOMAINS = {"example.com", "example.org"}
STRIPE_PANS = {
    "4242424242424242", "5555555555554444", "378282246310005",
    "6011111111111117", "4000000000000002", "4000000000009995",
    "4000000000000069", "4000000000000127",
}


def test_ssns_are_from_never_issued_areas(manifest: dict) -> None:
    for row in manifest["documents"]:
        text = (GOLD / row["path"]).read_text(encoding="utf-8")
        for m in _SSN.finditer(text):
            area = int(m.group(1))
            assert area == 0 or area == 666 or 900 <= area <= 999, (
                f"{row['doc_id']} contains SSN area {area}, which SSA can issue")


def test_email_domains_are_reserved(manifest: dict) -> None:
    for row in manifest["documents"]:
        text = (GOLD / row["path"]).read_text(encoding="utf-8")
        for m in _EMAIL.finditer(text):
            assert m.group(1) in ALLOWED_DOMAINS, (
                f"{row['doc_id']} uses non-reserved domain {m.group(1)}")


def test_valid_card_numbers_are_documented_stripe_test_values(
        manifest: dict) -> None:
    from scripts.analyst_benchmark.detectors import luhn_ok
    for row in manifest["documents"]:
        text = (GOLD / row["path"]).read_text(encoding="utf-8")
        for m in re.finditer(r"\b\d{13,19}\b", text):
            if luhn_ok(m.group()):
                assert m.group() in STRIPE_PANS, (
                    f"{row['doc_id']} has Luhn-valid PAN {m.group()} outside "
                    "the documented Stripe test set")


def test_phone_numbers_use_the_fiction_range(manifest: dict) -> None:
    for row in manifest["documents"]:
        text = (GOLD / row["path"]).read_text(encoding="utf-8")
        for m in re.finditer(r"\b555-(\d{4})\b", text):
            assert 100 <= int(m.group(1)) <= 199, (
                f"{row['doc_id']} uses 555-{m.group(1)}, outside 555-0100..0199")


def test_manifest_records_identifier_provenance(manifest: dict) -> None:
    prov = manifest["identifier_provenance"]
    assert "stripe.com" in prov["card_pans"]
    assert "stripe.com" in prov["ach_routing"]
    for key in ("ssn", "phone", "email", "names_streets_orgs"):
        assert prov.get(key)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
def test_regeneration_is_byte_identical(tmp_path: Path) -> None:
    """The generator is committed alongside its output; they must agree."""
    before = {p.name: p.read_bytes() for p in sorted(GOLD.glob("docs/*.txt"))}
    before_manifest = MANIFEST.read_bytes()
    cp = subprocess.run([sys.executable, str(GOLD / "generate.py")],
                        capture_output=True, text=True, check=False,
                        cwd=str(REPO_ROOT), shell=False)
    assert cp.returncode == 0, cp.stderr
    after = {p.name: p.read_bytes() for p in sorted(GOLD.glob("docs/*.txt"))}
    assert after == before, "generator output drifted from the committed fixtures"
    assert MANIFEST.read_bytes() == before_manifest


def test_screening_subset_is_balanced(manifest: dict) -> None:
    by_id = {r["doc_id"]: r for r in manifest["documents"]}
    subset = manifest["screening_subset"]
    assert len(subset) == 44
    strata = Counter(by_id[d]["stratum"] for d in subset)
    assert strata["positive_control"] == 24
    assert strata["negative_clean"] == 6
    assert strata["negative_near_miss"] == 6
    assert strata["injection"] == 4
    assert strata["injection_clean_twin"] == 4
    per_cat = Counter()
    for d in subset:
        if by_id[d]["stratum"] == "positive_control":
            for c in by_id[d]["categories_present"]:
                per_cat[c] += 1
    assert dict(per_cat) == {"pii": 6, "financial": 6,
                             "contact": 6, "demographic": 6}
