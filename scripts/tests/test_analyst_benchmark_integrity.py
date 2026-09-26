"""Regression tests for the C0B-1 scoring/result-integrity repair."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.analyst_benchmark import (client, goldset, ledger, metrics,
                                       rescore_c0b1, stages)


class _Envelope:
    def as_dict(self):
        return {}


class _AlwaysValidClient:
    def generate(self, model, prompt, ws, opts):
        raw = json.dumps({
            "document_type": "record",
            "subject": "",
            "assessment": "no_findings",
            "findings": [],
        })
        return client.CallResult(
            ok=True, model=model, outcome="ok", content=raw,
            content_bytes=len(raw), total_bytes=len(raw))

    def ps(self):
        return {}


class _RetryClient:
    def __init__(self, accepted: str):
        self.accepted = accepted
        self.scored_calls = 0

    def generate(self, model, prompt, ws, opts):
        if opts.num_predict == 32:  # warm-up
            return client.CallResult(
                ok=True, model=model, outcome="ok", content="{}")
        self.scored_calls += 1
        if self.scored_calls == 1:
            return client.CallResult(
                ok=False, model=model, outcome="truncated", content="",
                done_reason="length")
        return client.CallResult(
            ok=True, model=model, outcome="ok", content=self.accepted,
            content_bytes=len(self.accepted), total_bytes=len(self.accepted))

    def ps(self):
        return {}


def _one_doc_set(doc_id: str) -> goldset.GoldSet:
    full = goldset.load()
    return goldset.GoldSet(
        version=full.version,
        chunk_chars_reference=full.chunk_chars_reference,
        docs=full.docs,
        screening_subset=[doc_id],
        provenance=full.provenance,
    )


def test_repeated_quote_uses_leftmost_span_not_model_offset() -> None:
    source = "token then token"
    second = source.rindex("token")
    verdict = metrics.ground_finding("token", second, source)

    assert verdict.grounded
    assert verdict.canonical_offset == 0
    assert verdict.canonical_end == 5
    assert verdict.match_count == 2
    assert verdict.model_offset_exact
    assert verdict.reason == "ok_multiple_matches_leftmost"


def test_current_manifest_injection_order_is_scored_after_collection(
        monkeypatch) -> None:
    corpus = goldset.load()
    subset_ids = corpus.screening_subset
    assert subset_ids.index("inj_01") < subset_ids.index("inj_twin_01")
    captured = []
    monkeypatch.setattr(stages.report, "append_raw_jsonl",
                        lambda _run, _name, row: captured.append(row))
    monkeypatch.setattr(stages.resources, "sample", lambda *_args: _Envelope())

    cells = stages.run_stage_b(
        _AlwaysValidClient(), corpus, ["fake"], ["v2"],
        ledger.Ledger(hard_cap=100, soft_wall_seconds=1000), "run",
        seed=1, opts_base=client.GenOptions())

    stats = cells["fake|v2"]
    assert stats.injection_pairs_measured == 4
    assert stats.injection_pairs_unmeasured == 0
    assert stats.robustness_failures == 0
    assert len(captured) == 44


def test_retry_persists_rejected_and_accepted_attempts(monkeypatch) -> None:
    corpus = _one_doc_set("pos_pii_001")
    doc = corpus.docs["pos_pii_001"]
    quote = doc.expected_identifiers[0]
    accepted = json.dumps({
        "document_type": "record",
        "subject": "",
        "assessment": "findings_present",
        "findings": [{"category": "pii", "quote": quote, "offset": 0}],
    })
    captured = []
    monkeypatch.setattr(stages.report, "append_raw_jsonl",
                        lambda _run, _name, row: captured.append(row))
    monkeypatch.setattr(stages.resources, "sample", lambda *_args: _Envelope())

    cells = stages.run_stage_b(
        _RetryClient(accepted), corpus, ["fake"], ["v2"],
        ledger.Ledger(hard_cap=20, soft_wall_seconds=1000), "run",
        seed=1, opts_base=client.GenOptions())

    assert [(row["attempt"], row["valid"], row["accepted_for_scoring"],
             row["final_attempt"]) for row in captured] == [
        (1, False, False, False),
        (2, True, True, True),
    ]
    assert captured[1]["raw_response"] == accepted
    stats = cells["fake|v2"]
    assert stats.valid_after_retry == 1
    assert stats.findings_grounded == 1  # the harness located the quote


def test_robustness_failure_fails_screening() -> None:
    verdict = metrics.screen(
        "m|v2", valid_first_pass=44, calls=44, grounded=100, findings=100,
        injection_events=0, robustness_failures=1)
    assert not verdict.passed
    assert verdict.reasons == ["injection_robustness_failures 1"]


def test_historical_rescore_never_turns_injection_into_pass(tmp_path: Path) -> None:
    full = goldset.load()
    inj = full.docs["inj_01"]
    twin = full.docs["inj_twin_01"]
    positive = full.docs["pos_pii_001"]
    corpus = goldset.GoldSet(
        version=full.version,
        chunk_chars_reference=full.chunk_chars_reference,
        docs=full.docs,
        screening_subset=[inj.doc_id, twin.doc_id, positive.doc_id],
        provenance=full.provenance,
    )
    valid = json.dumps({
        "document_type": "record", "subject": "",
        "assessment": "no_findings", "findings": [],
    })
    rows = [
        {"cell": "fake|v2", "doc_id": inj.doc_id, "valid": True,
         "raw_response": valid},
        {"cell": "fake|v2", "doc_id": twin.doc_id, "valid": True,
         "raw_response": valid},
        # Reproduce the legacy retry defect: labelled valid, but this is the
        # rejected first attempt and the accepted retry is absent.
        {"cell": "fake|v2", "doc_id": positive.doc_id, "valid": True,
         "raw_response": ""},
    ]
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    result = rescore_c0b1.rescore(raw, gs=corpus)
    cell = result.cells["fake|v2"]
    assert cell.injection_status == rescore_c0b1.INJECTION_INVALID
    assert cell.injection_pairs_available == 1
    assert cell.accepted_responses_missing == [positive.doc_id]
    assert "injection pairing was order-dependent" in result.execution_defects[0]
