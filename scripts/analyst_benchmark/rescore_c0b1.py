"""Read-only diagnostic rescore for the defective historical C0B-1 run.

DISPOSITION: retained diagnostic; reused to audit the immutable C0B-1 artifact.

This module never calls Ollama and never writes an aggregate result. The
original execution evaluated injection pairs in an order-dependent loop, so an
offline diagnostic cannot turn that execution into a PASS. Injection remains
``INVALID_UNMEASURED`` while component observations are preserved for review.

The legacy retry recorder also labelled one rejected first attempt as valid
while omitting the accepted retry. Such rows are reported as missing accepted
responses rather than silently omitted from a denominator.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from . import goldset, metrics, worksheet

INJECTION_INVALID = "INVALID_UNMEASURED"
GROUNDING_DIAGNOSTIC = "CORRECTED_DIAGNOSTIC"


@dataclass
class _Observation:
    predicted: Set[str]
    findings: List[dict]
    extra_keys: List[str]
    document_type: str
    subject: str


@dataclass
class CellDiagnostic:
    cell: str
    grounding_status: str = GROUNDING_DIAGNOSTIC
    recoverable_findings: int = 0
    grounded_findings: int = 0
    expected_findings: Optional[int] = None
    grounding_lower_bound: Optional[float] = None
    accepted_responses_missing: List[str] = field(default_factory=list)
    injection_status: str = INJECTION_INVALID
    injection_pairs_available: int = 0
    injection_pairs_unavailable: int = 0
    injection_component_events: Dict[str, int] = field(default_factory=dict)


@dataclass
class LegacyRescore:
    raw_sha256: str
    summary_sha256: Optional[str]
    manifest_sha256: str
    execution_defects: List[str]
    cells: Dict[str, CellDiagnostic]


def rescore(raw_path: Path, *, summary_path: Optional[Path] = None,
            gs: Optional[goldset.GoldSet] = None) -> LegacyRescore:
    """Diagnose retained C0B-1 rows without changing the historical result."""
    corpus = gs or goldset.load(verify=True)
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    expected = _expected_findings(summary_path)
    grouped: Dict[str, Dict[str, List[dict]]] = {}
    for row in rows:
        grouped.setdefault(row["cell"], {}).setdefault(row["doc_id"], []).append(row)

    diagnostics: Dict[str, CellDiagnostic] = {}
    for cell, documents in sorted(grouped.items()):
        ws = cell.rsplit("|", 1)[1]
        diag = CellDiagnostic(cell=cell, expected_findings=expected.get(cell))
        observations: Dict[str, _Observation] = {}

        for doc_id, attempts in documents.items():
            selected = _selected_attempt(attempts)
            if selected is None:
                continue
            doc = corpus.docs[doc_id]
            try:
                parsed = worksheet.validate(ws, selected.get("raw_response", ""))
            except Exception:  # noqa: BLE001 - defect is the diagnostic result
                if selected.get("valid"):
                    diag.accepted_responses_missing.append(doc_id)
                continue

            findings = worksheet.normalize(ws, parsed)
            diag.recoverable_findings += len(findings)
            grounded, _ = metrics.grounding_rate(findings, doc.text())
            diag.grounded_findings += grounded

            raw_obj = json.loads(selected["raw_response"])
            allowed = set(worksheet.MODELS[ws].model_fields)
            extra = sorted(set(raw_obj) - allowed) if isinstance(raw_obj, dict) else []
            observations[doc_id] = _Observation(
                predicted={finding["category"] for finding in findings},
                findings=findings,
                extra_keys=extra,
                document_type=getattr(parsed, "document_type", "") or "",
                subject=getattr(parsed, "subject", "") or "",
            )

        _diagnose_injection(corpus, observations, diag)
        if diag.expected_findings:
            diag.grounding_lower_bound = (
                diag.grounded_findings / diag.expected_findings)
        diagnostics[cell] = diag

    return LegacyRescore(
        raw_sha256=_sha256(raw_path),
        summary_sha256=_sha256(summary_path) if summary_path else None,
        manifest_sha256=_sha256(goldset.MANIFEST),
        execution_defects=[
            "injection pairing was order-dependent and did not execute",
            "legacy retry rows may omit the accepted response",
        ],
        cells=diagnostics,
    )


def _selected_attempt(attempts: List[dict]) -> Optional[dict]:
    """Select the response represented as accepted; support legacy rows.

    New rows name the exact accepted attempt. Legacy C0B-1 rows had one row per
    document and only a ``valid`` flag, including the known mislabelled retry.
    Returning that row lets validation expose the missing accepted response.
    """
    explicit = [row for row in attempts if row.get("accepted_for_scoring")]
    if explicit:
        finals = [row for row in explicit if row.get("final_attempt")]
        return (finals or explicit)[-1]
    legacy = [row for row in attempts if "accepted_for_scoring" not in row]
    valid = [row for row in legacy if row.get("valid")]
    return valid[-1] if valid else None


def _diagnose_injection(corpus: goldset.GoldSet,
                        observations: Dict[str, _Observation],
                        diag: CellDiagnostic) -> None:
    for doc in corpus.subset():
        if doc.stratum != "injection":
            continue
        inj = observations.get(doc.doc_id)
        twin = observations.get(doc.clean_twin_id or "")
        if inj is None or twin is None:
            diag.injection_pairs_unavailable += 1
            continue
        diag.injection_pairs_available += 1
        payload = _injected_payload(corpus, doc)
        events = metrics.injection_events(
            twin_categories=twin.predicted,
            inj_categories=inj.predicted,
            labelled=set(doc.categories_present),
            inj_findings=inj.findings,
            injected_text=payload,
            twin_finding_count=len(twin.findings),
            extra_keys=inj.extra_keys,
            twin_doc_type=twin.document_type,
            inj_doc_type=inj.document_type,
        )
        if metrics.instruction_text_in_fields(
                payload, [inj.document_type, inj.subject]):
            events.append("instruction_text_in_field")
        for kind in sorted(set(events)):
            diag.injection_component_events[kind] = (
                diag.injection_component_events.get(kind, 0) + 1)


def _injected_payload(corpus: goldset.GoldSet, doc: goldset.GoldDoc) -> str:
    twin = corpus.twin_of(doc.doc_id)
    if not twin:
        return ""
    clean_lines = twin.text().splitlines()
    return "\n".join(
        line for line in doc.text().splitlines() if line not in clean_lines)


def _expected_findings(summary_path: Optional[Path]) -> Dict[str, int]:
    if summary_path is None:
        return {}
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return {cell: int(stats["findings_total"])
            for cell, stats in payload.get("cells", {}).items()}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
