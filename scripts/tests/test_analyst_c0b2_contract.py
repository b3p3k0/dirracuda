"""Offline contract tests for the C0B-2A schema, manifest and Stage-C plan."""
from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.analyst_benchmark import chunker, goldset, leakscan
from scripts.analyst_benchmark import c0b2_plan as plan
from scripts.analyst_benchmark import c0b2_schema as schema
from scripts.analyst_benchmark import c0b2_leakscan, c0b2_runtime


def _evidence(quote: str = "900-12-3456", offset: object = 0) -> dict:
    return {"quote": quote, "offset": offset}


def _v1(*, assessment: str = "findings_present",
        evidence: list[dict] | None = None) -> dict:
    rows = []
    for category in schema.CATEGORIES:
        values = (evidence if category == "pii" else [])
        if evidence is None:
            values = [_evidence()] if category == "pii" else []
        rows.append({"category": category, "present": bool(values),
                     "evidence": values})
    return {"document_type": "record", "subject": "subject",
            "assessment": assessment, "categories": rows}


def _v2(*, assessment: str = "findings_present",
        findings: list[dict] | None = None) -> dict:
    if findings is None:
        findings = [{"category": "pii", **_evidence()}]
    return {"document_type": "record", "subject": "subject",
            "assessment": assessment, "findings": findings}


@pytest.mark.parametrize("version,value", [("v1", _v1()), ("v2", _v2())])
def test_valid_strict_worksheets_round_trip_canonically(version, value) -> None:
    parsed = schema.validate(version, schema.canonical_json(value))
    assert schema.validate(version, parsed.model_dump()) == parsed
    assert schema.canonical_json(parsed) == schema.canonical_json(value)


@pytest.mark.parametrize("version,value", [
    ("v1", {**_v1(), "unexpected": True}),
    ("v2", {**_v2(), "unexpected": True}),
])
def test_extra_fields_are_forbidden_at_top_level(version, value) -> None:
    with pytest.raises(ValidationError):
        schema.validate(version, value)


@pytest.mark.parametrize("version,value", [
    ("v1", _v1(evidence=[_evidence(offset=True)])),
    ("v2", _v2(findings=[{"category": "pii", **_evidence(offset="0")}])),
])
def test_strict_types_do_not_coerce(version, value) -> None:
    with pytest.raises(ValidationError):
        schema.validate(version, value)


def test_nested_extra_fields_are_forbidden() -> None:
    value = _v2()
    value["findings"][0]["extra"] = "no"
    with pytest.raises(ValidationError):
        schema.validate("v2", value)


@pytest.mark.parametrize("field,value", [
    ("document_type", ""),
    ("document_type", "x" * 81),
    ("subject", "x" * 161),
])
def test_string_bounds(field, value) -> None:
    answer = _v2()
    answer[field] = value
    with pytest.raises(ValidationError):
        schema.validate("v2", answer)


def test_quote_and_list_bounds() -> None:
    too_long = _v2(findings=[{"category": "pii", **_evidence("x" * 241)}])
    too_many = _v2(findings=[
        {"category": "pii", **_evidence(f"quote-{index}")}
        for index in range(17)
    ])
    for answer in (too_long, too_many):
        with pytest.raises(ValidationError):
            schema.validate("v2", answer)


def test_v1_requires_canonical_complete_category_order() -> None:
    missing = _v1()
    missing["categories"].pop()
    reversed_rows = _v1()
    reversed_rows["categories"].reverse()
    for answer in (missing, reversed_rows):
        with pytest.raises(ValidationError):
            schema.validate("v1", answer)


def test_v1_present_must_match_evidence() -> None:
    answer = _v1()
    answer["categories"][0]["present"] = False
    with pytest.raises(ValidationError):
        schema.validate("v1", answer)


@pytest.mark.parametrize("version,value", [
    ("v1", _v1(assessment="no_findings")),
    ("v2", _v2(assessment="insufficient_evidence")),
    ("v1", _v1(assessment="findings_present", evidence=[])),
    ("v2", _v2(assessment="findings_present", findings=[])),
])
def test_assessment_semantics_reject_inconsistent_findings(version, value) -> None:
    with pytest.raises(ValidationError):
        schema.validate(version, value)


def test_empty_findings_are_valid_for_both_absence_assessments() -> None:
    assert schema.validate("v1", _v1(assessment="no_findings", evidence=[]))
    assert schema.validate(
        "v2", _v2(assessment="insufficient_evidence", findings=[]))


def test_unicode_equivalent_duplicate_quotes_are_rejected() -> None:
    decomposed = "Cafe\u0301"
    composed = unicodedata.normalize("NFC", decomposed)
    answer = _v2(findings=[
        {"category": "contact", **_evidence(decomposed)},
        {"category": "contact", **_evidence(composed)},
    ])
    with pytest.raises(ValidationError):
        schema.validate("v2", answer)


def test_schema_and_json_hashes_are_order_stable() -> None:
    assert schema.stable_hash({"b": 2, "a": 1}) == schema.stable_hash({"a": 1, "b": 2})
    assert schema.canonical_json({"é": 1}) == b'{"\xc3\xa9":1}'
    assert len(schema.schema_hash("v1")) == 64
    assert schema.schema_hash("v1") != schema.schema_hash("v2")


def test_prompt_rejects_a_source_that_contains_its_nonce() -> None:
    with pytest.raises(ValueError):
        schema.build_prompt("v2", "contains FENCE_TEST", "FENCE_TEST")


def test_frozen_split_is_a_disjoint_44_50_72_cover() -> None:
    corpus = goldset.load()
    split = plan.frozen_split(corpus)
    assert tuple(map(len, (split.c, split.d, split.f))) == (44, 50, 72)
    ids = split.c + split.d + split.f
    assert len(ids) == len(set(ids)) == len(corpus.docs) == 166
    assert set(ids) == set(corpus.docs)
    assert split.c == tuple(corpus.screening_subset)


def test_split_strata_match_the_frozen_contract() -> None:
    corpus = goldset.load()
    split = plan.frozen_split(corpus)
    counts = {
        name: Counter(corpus.docs[doc_id].stratum for doc_id in ids)
        for name, ids in (("c", split.c), ("d", split.d), ("f", split.f))
    }
    assert counts["c"] == Counter({
        "positive_control": 24, "negative_clean": 6,
        "negative_near_miss": 6, "injection": 4,
        "injection_clean_twin": 4,
    })
    assert counts["d"] == Counter({
        "positive_control": 24, "negative_clean": 6,
        "negative_near_miss": 6, "boundary": 12,
        "output_truncation": 1, "input_truncation": 1,
    })
    assert counts["f"] == Counter({
        "positive_control": 32, "negative_clean": 8,
        "negative_near_miss": 8, "injection": 4,
        "injection_clean_twin": 4, "boundary": 12,
        "output_truncation": 2, "input_truncation": 2,
    })


def test_boundary_views_are_deterministic_two_chunk_derivatives() -> None:
    corpus = goldset.load()
    for doc in corpus.by_stratum("boundary"):
        for size in plan.CHUNK_CANDIDATES:
            first = plan.boundary_view(doc, size)
            second = plan.boundary_view(doc, size)
            chunks = chunker.chunk(first.text, chunk_chars=size,
                                   overlap_chars=plan.OVERLAP)
            assert first == second
            assert len(first.text) == size + 512
            assert first.text.index(first.expected_identifier) == size - first.split_offset
            assert first.text.count(first.expected_identifier) == 1
            assert len(chunks) == 2
            assert any(first.expected_identifier in part.text for part in chunks)


def test_manifest_freezes_all_boundary_hashes_and_unique_markers() -> None:
    corpus = goldset.load()
    manifest = plan.build_master_manifest(corpus)
    assert len(manifest.boundary_views) == 24 * 3
    assert manifest.boundary_generator_version == "c0b2-boundary-v1"
    assert manifest.boundary_filler_byte == "N"
    assert len({(v.doc_id, v.chunk_chars) for v in manifest.boundary_views}) == 72
    assert len({v.sha256 for v in manifest.boundary_views}) == 72
    plan.validate_markers(corpus)
    for owner, marker in manifest.markers.items():
        assert corpus.docs[owner].text().count(marker) == 1
        assert sum(doc.text().count(marker) for doc in corpus.docs.values()) == 1


def test_stage_c_plan_has_264_unique_stable_work_items() -> None:
    key = bytes(range(32))
    first = plan.build_c_stage_plan(key)
    second = plan.build_c_stage_plan(key)
    changed = plan.build_c_stage_plan(bytes(reversed(range(32))))
    assert first == second
    assert first.sha256 != changed.sha256
    assert len(first.work) == len({item.work_id for item in first.work}) == 264
    assert len({item.cell_id for item in first.work}) == 6
    assert all(len(item.request_sha256) == 64 for item in first.work)


def test_stage_c_nonces_are_model_shared_and_pair_shared() -> None:
    stage = plan.build_c_stage_plan(b"K" * 32)
    by_ws_doc = defaultdict(set)
    for item in stage.work:
        by_ws_doc[(item.worksheet, item.doc_id)].add(item.nonce)
    assert all(len(values) == 1 for values in by_ws_doc.values())
    for worksheet in plan.WORKSHEETS:
        for number in range(1, 5):
            injected = next(iter(by_ws_doc[(worksheet, f"inj_{number:02d}")]))
            twin = next(iter(by_ws_doc[(worksheet, f"inj_twin_{number:02d}")]))
            assert injected == twin
    assert all(value.startswith("FENCE_") and len(value) == 38
               for values in by_ws_doc.values() for value in values)


def test_request_hash_binds_exact_model_prompt_and_schema() -> None:
    stage = plan.build_c_stage_plan(b"R" * 32)
    grouped = defaultdict(list)
    for item in stage.work:
        grouped[(item.worksheet, item.doc_id)].append(item)
    sample = grouped[("v2", "pos_pii_001")]
    assert len({item.nonce for item in sample}) == 1
    assert len({item.prompt_sha256 for item in sample}) == 1
    assert len({item.request_sha256 for item in sample}) == 3
    assert len({item.work_id for item in sample}) == 3


def test_complete_public_allowlist_matches_reviewed_protocol() -> None:
    protocol = Path("docs/dev/ollama_integration/BENCHMARK_PROTOCOL_C0B2.md").read_text()
    section = protocol.split(
        "### 18.6 Complete-public implementation allowlist", 1)[1].split(
        "### 18.7 Card sequence and live gate", 1)[0]
    additions = {line[3:-1] for line in section.splitlines()
                 if line.startswith("- `") and line.endswith("`")}
    expected = (c0b2_leakscan.FROZEN_C0B2A_PATHS
                | c0b2_leakscan.FROZEN_C0B2B1_PATHS | additions)
    assert c0b2_leakscan.FROZEN_C0B2_PUBLIC_PATHS == expected
    assert len(expected) == 48
    assert expected == leakscan.ALLOWLIST_EXACT
    assert leakscan.allowed_c0b1("scripts/analyst_benchmark/client.py")
    assert not leakscan.allowed("scripts/analyst_benchmark/client.py")


def test_safe_task_reader_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("public evidence\n")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(c0b2_leakscan.LeakGateError):
        c0b2_leakscan.read_regular_file(link, trusted_root=tmp_path)
    with pytest.raises(c0b2_leakscan.LeakGateError):
        c0b2_runtime._sha256_file(link, trusted_root=tmp_path)


def test_safe_task_reader_rejects_name_swap_during_read(
        tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "task.txt"
    replacement = tmp_path / "replacement.txt"
    displaced = tmp_path / "displaced.txt"
    target.write_bytes(b"first public file")
    replacement.write_bytes(b"second public file")
    original_read = c0b2_leakscan.os.read
    swapped = False

    def swapping_read(fd: int, size: int) -> bytes:
        nonlocal swapped
        block = original_read(fd, size)
        if block and not swapped:
            swapped = True
            target.replace(displaced)
            replacement.replace(target)
        return block

    monkeypatch.setattr(c0b2_leakscan.os, "read", swapping_read)
    with pytest.raises(c0b2_leakscan.LeakGateError,
                       match="changed while being read"):
        c0b2_leakscan.read_regular_file(target, trusted_root=tmp_path)


def test_safe_task_reader_rejects_intermediate_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "task.txt").write_text("public evidence\n")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(c0b2_leakscan.LeakGateError):
        c0b2_leakscan.read_regular_file(
            linked / "task.txt", trusted_root=tmp_path)


def test_safe_task_reader_rejects_trusted_root_dotdot_escape(
        tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be read\n")

    with pytest.raises(c0b2_leakscan.LeakGateError,
                       match="unsafe lexical component"):
        c0b2_leakscan.read_regular_file(
            trusted / ".." / outside.name, trusted_root=trusted)


def test_safe_task_reader_rejects_intermediate_swap_during_read(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "parent"
    replacement = tmp_path / "replacement"
    displaced = tmp_path / "displaced"
    parent.mkdir()
    replacement.mkdir()
    (parent / "task.txt").write_text("first public file\n")
    (replacement / "task.txt").write_text("other public file\n")
    original_read = c0b2_leakscan.os.read
    swapped = False

    def swapping_read(fd: int, size: int) -> bytes:
        nonlocal swapped
        block = original_read(fd, size)
        if block and not swapped:
            swapped = True
            parent.replace(displaced)
            replacement.replace(parent)
        return block

    monkeypatch.setattr(c0b2_leakscan.os, "read", swapping_read)
    with pytest.raises(c0b2_leakscan.LeakGateError,
                       match="directory changed while being read"):
        c0b2_leakscan.read_regular_file(
            parent / "task.txt", trusted_root=tmp_path)


def test_content_scan_is_bound_to_captured_inventory(
        tmp_path: Path, monkeypatch) -> None:
    relative = "scripts/analyst_benchmark/c0b2_cli.py"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("first public file\n")
    monkeypatch.setattr(leakscan, "REPO_ROOT", tmp_path)
    inventory = {relative: leakscan._metadata(relative, " M")}
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("other public file\n")
    replacement.replace(target)

    with pytest.raises(leakscan.BaselineError,
                       match="changed after inventory capture"):
        leakscan.scan_content([relative], ["long enough raw model response"],
                              inventory=inventory)


def test_generation_pin_binds_c_d_and_f_factor_domains(monkeypatch) -> None:
    from scripts.analyst_benchmark import c0b2_stage_d_plan, c0b2_stage_f_plan

    original = c0b2_leakscan.public_generation_options_sha256()
    monkeypatch.setattr(plan, "KEEP_ALIVE", "changed")
    assert c0b2_leakscan.public_generation_options_sha256() != original
    monkeypatch.undo()
    budgets = dict(c0b2_stage_d_plan._D1_BUDGETS)
    budgets["gpt-oss:20b"] = (*budgets["gpt-oss:20b"], 8192)
    monkeypatch.setattr(c0b2_stage_d_plan, "_D1_BUDGETS", budgets)
    assert c0b2_leakscan.public_generation_options_sha256() != original
    monkeypatch.undo()
    monkeypatch.setattr(c0b2_stage_f_plan, "SEEDS", (1, 17, 20260804, 99))
    assert c0b2_leakscan.public_generation_options_sha256() != original


def test_source_pins_name_public_schema_and_all_stage_scorers(
        monkeypatch, tmp_path: Path) -> None:
    seen = []
    monkeypatch.setattr(c0b2_runtime, "_sha256_file",
                        lambda path, **_kwargs: seen.append(Path(path).name) or "a" * 64)
    monkeypatch.setattr(c0b2_runtime, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(c0b2_runtime, "_task_tree_hash", lambda _root: "c" * 64)
    monkeypatch.setattr(c0b2_runtime, "public_generation_options_sha256",
                        lambda: "d" * 64)
    seal = c0b2_leakscan.WorktreeSeal("b" * 40, ())

    pins = c0b2_runtime._source_pins(tmp_path, seal)

    assert {"c0b2_public_schema.py", "c0b2_public_scoring.py",
            "c0b2_stage_c.py", "c0b2_stage_d.py", "c0b2_stage_f.py"} <= set(seen)
    assert pins["generation_options_sha256"] == "d" * 64
