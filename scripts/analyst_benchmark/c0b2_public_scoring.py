"""Pure common derivations for public C0B-2 D/F evidence.

The functions here deliberately accept bounded, already-loaded values.  They never open
fixtures/checkpoints, contact a model, or build D/F-specific quality aggregates.

DISPOSITION: benchmark-only shared scoring substrate; remove after C0B acceptance.
"""
from __future__ import annotations

import hashlib
import hmac
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel

from .c0b2_schema import CATEGORIES, canonical_json
from .c0b2_public_schema import ExactFraction, Nonce, sha256_json
from .c0b2_stage_c import classify_answer
from .metrics import ground_finding


class PublicScoringError(RuntimeError):
    """Stored public evidence or a requested derivation violates the frozen catalog."""


def fraction_payload(value: Fraction | int) -> dict[str, int]:
    """Return the catalog's reduced exact-fraction object."""
    if not isinstance(value, Fraction) and type(value) is not int:
        raise TypeError("fraction payload requires an exact integer or Fraction")
    exact = value if isinstance(value, Fraction) else Fraction(value, 1)
    return {"numerator": exact.numerator, "denominator": exact.denominator}


def fraction_value(value: ExactFraction | Mapping[str, Any]) -> Fraction:
    """Strictly read a persisted exact fraction."""
    parsed = (value if isinstance(value, ExactFraction)
              else ExactFraction.model_validate(value, strict=True))
    return Fraction(parsed.numerator, parsed.denominator)


def safe_fraction(numerator: int, denominator: int) -> Fraction:
    """Use the frozen zero-denominator convention without Boolean coercion."""
    if type(numerator) is not int or type(denominator) is not int:
        raise TypeError("fraction counts must be exact integers")
    if numerator < 0 or denominator < 0:
        raise ValueError("fraction counts must be nonnegative")
    return Fraction(numerator, denominator) if denominator else Fraction(0, 1)


def f1_fraction(tp: int, fp: int, fn: int) -> Fraction:
    """Compute exact document-level F1 using the frozen formula."""
    _counts(tp, fp, fn)
    return safe_fraction(2 * tp, 2 * tp + fp + fn)


def category_metric(tp: int, fp: int, fn: int) -> dict[str, Any]:
    """Build one exact six-key category metric row."""
    _counts(tp, fp, fn)
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": fraction_payload(safe_fraction(tp, tp + fp)),
        "recall": fraction_payload(safe_fraction(tp, tp + fn)),
        "f1": fraction_payload(f1_fraction(tp, fp, fn)),
    }


def derive_category_metrics(
        expected: Sequence[Sequence[str]], predicted: Sequence[Sequence[str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int | dict[str, int]]]:
    """Derive ordered per-category rows plus exact macro/micro F1 payloads."""
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted document rows differ in length")
    counts: dict[str, tuple[int, int, int]] = {}
    for category in CATEGORIES:
        tp = fp = fn = 0
        for expected_row, predicted_row in zip(expected, predicted, strict=True):
            expected_set = _category_set(expected_row)
            predicted_set = _category_set(predicted_row)
            has_expected = category in expected_set
            has_predicted = category in predicted_set
            tp += int(has_expected and has_predicted)
            fp += int(not has_expected and has_predicted)
            fn += int(has_expected and not has_predicted)
        counts[category] = (tp, fp, fn)
    metrics = {category: category_metric(*counts[category]) for category in CATEGORIES}
    macro = sum((f1_fraction(*counts[category]) for category in CATEGORIES), Fraction()) / 4
    total_tp = sum(row[0] for row in counts.values())
    total_fp = sum(row[1] for row in counts.values())
    total_fn = sum(row[2] for row in counts.values())
    summary: dict[str, int | dict[str, int]] = {
        "macro_f1": fraction_payload(macro),
        "micro_f1": fraction_payload(f1_fraction(total_tp, total_fp, total_fn)),
    }
    return metrics, summary


def ordered_reasons(order: Sequence[str], failures: Mapping[str, bool]) -> list[str]:
    """Return the unique frozen-order reasons whose exact Boolean predicate failed."""
    if len(set(order)) != len(order):
        raise ValueError("reason order contains duplicates")
    unknown = set(failures) - set(order)
    if unknown:
        raise ValueError("unknown failure reasons: " + ", ".join(sorted(unknown)))
    if any(type(value) is not bool for value in failures.values()):
        raise TypeError("failure predicates must be exact Booleans")
    return [reason for reason in order if failures.get(reason, False)]


def validate_ordered_subset(values: Sequence[str], order: Sequence[str],
                            *, label: str) -> tuple[str, ...]:
    """Reject duplicate, unknown, or out-of-order enum arrays."""
    try:
        indices = [order.index(value) for value in values]
    except ValueError as exc:
        raise PublicScoringError(f"{label} contains an unknown value") from exc
    if indices != sorted(set(indices)):
        raise PublicScoringError(f"{label} must be unique and frozen-order")
    return tuple(values)


def answered_headroom(prompt_eval_counts: Sequence[int], *, num_predict: int,
                       num_ctx: int) -> tuple[int | None, bool]:
    """Evaluate context headroom across every bounded HTTP answer."""
    _counts(num_predict, num_ctx)
    if num_ctx == 0:
        raise ValueError("context must be positive")
    counts = list(prompt_eval_counts)
    if any(type(value) is not int or value < 0 for value in counts):
        raise TypeError("prompt counts must be nonnegative exact integers")
    if not counts:
        return None, False
    limit = (85 * num_ctx) // 100
    return max(counts), all(value + num_predict <= limit for value in counts)


def count_headroom_violating_work(
        answered_by_work: Mapping[str, Sequence[int]], *, num_predict: int,
        num_ctx: int,
) -> int:
    """Count work items, not attempts, having any answered headroom violation."""
    violations = 0
    for work_id, counts in answered_by_work.items():
        if not isinstance(work_id, str) or not work_id:
            raise TypeError("work IDs must be nonempty strings")
        maximum, passed = answered_headroom(
            counts, num_predict=num_predict, num_ctx=num_ctx)
        if maximum is None:
            raise PublicScoringError(
                "headroom evidence requires at least one bounded HTTP answer")
        if not passed:
            violations += 1
    return violations


def derive_health_answer_evidence(
        attempts: Sequence[Mapping[str, Any]], *, worksheet: str, source: str,
        num_predict: int, num_ctx: int,
) -> dict[str, Any]:
    """Rebuild the answer-derived cancellation-health fields from attempt history."""
    if worksheet not in {"v1", "v2"} or type(source) is not str:
        raise ValueError("health worksheet/source types are invalid")
    if not attempts:
        raise PublicScoringError("health evidence requires an attempt history")
    answered: list[tuple[Mapping[str, Any], Any]] = []
    attempt_ids: set[str] = set()
    allowed_states = {
        "ACCEPTED", "SCHEMA_INVALID", "RETRYABLE_TRANSPORT",
        "ORPHANED_UNKNOWN", "CANCELLED_UNVERIFIED",
    }
    answered_count = 0
    history_closed = False
    for raw in attempts:
        if type(raw) is not dict or set(raw) != {
                "attempt_id", "state", "response", "metadata"}:
            raise PublicScoringError("health attempt has an invalid shape")
        attempt_id = raw["attempt_id"]
        _require_sha256(attempt_id, "health attempt ID")
        if attempt_id in attempt_ids:
            raise PublicScoringError("health attempt IDs must be unique")
        attempt_ids.add(attempt_id)
        state, response, metadata = raw["state"], raw["response"], raw["metadata"]
        if type(state) is not str or state not in allowed_states:
            raise PublicScoringError("health attempt has an unknown terminal state")
        if history_closed:
            raise PublicScoringError(
                "health attempt appears after an authoritative terminal answer")
        is_answered = state in {"ACCEPTED", "SCHEMA_INVALID"}
        if is_answered != (type(response) is str):
            raise PublicScoringError("health attempt response contradicts its state")
        if not is_answered:
            if response is not None or metadata is not None and type(metadata) is not dict:
                raise PublicScoringError(
                    "non-answered health attempt retained response-shaped evidence")
            continue
        if type(metadata) is not dict:
            raise PublicScoringError("answered health attempt metadata must be an object")
        done_reason = metadata.get("done_reason")
        prompt_count = metadata.get("prompt_eval_count")
        flags = tuple(metadata.get(name) for name in (
            "tools_empty", "images_empty", "unknown_message_fields_empty"))
        if (type(done_reason) is not str or not done_reason
                or type(prompt_count) is not int or prompt_count < 0
                or any(type(flag) is not bool for flag in flags)):
            raise PublicScoringError(
                "answered health attempt lacks exact terminal metadata")
        classification = classify_answer(worksheet, response)
        expected_state = "ACCEPTED" if classification.valid else "SCHEMA_INVALID"
        if state != expected_state:
            raise PublicScoringError(
                "health attempt state contradicts independent answer validation")
        answered.append((raw, classification))
        answered_count += 1
        if state == "ACCEPTED" or answered_count == 2:
            history_closed = True

    accepted = next(
        ((raw, result) for raw, result in answered if result.valid), None)
    retained_grounded_pii = False
    if accepted is not None:
        result = accepted[1]
        assert result.value is not None
        retained_grounded_pii = any(
            finding["category"] == "pii"
            and ground_finding(
                finding["quote"], finding["offset"], source).grounded
            for finding in _answer_findings(worksheet, result.value)
        )
    prompt_counts = [raw["metadata"]["prompt_eval_count"] for raw, _ in answered]
    maximum, headroom = answered_headroom(
        prompt_counts, num_predict=num_predict, num_ctx=num_ctx)
    authoritative_reason = (
        accepted[0]["metadata"]["done_reason"] if accepted is not None else None)
    return {
        "eventual_valid": accepted is not None,
        "retained_grounded_pii": retained_grounded_pii,
        "authoritative_done_reason": authoritative_reason,
        "max_answered_prompt_eval_count": maximum,
        "length_outcomes": sum(
            raw["metadata"]["done_reason"] == "length" for raw, _ in answered),
        "headroom_passed": headroom,
        "tools_empty": all(raw["metadata"]["tools_empty"] for raw, _ in answered),
        "images_empty": all(raw["metadata"]["images_empty"] for raw, _ in answered),
        "unknown_message_fields_empty": all(
            raw["metadata"]["unknown_message_fields_empty"] for raw, _ in answered),
        "schema_escape_empty": all(result.schema_escape_empty for _, result in answered),
    }


def document_view_identity(*, doc_id: str, document_sha256: str,
                           pair_id: str | None = None,
                           view_sha256: str | None = None) -> str:
    """Build the exact D/F nonce registry identity."""
    _require_text(doc_id, "document ID")
    _require_sha256(document_sha256, "document hash")
    if pair_id is not None:
        if view_sha256 is not None:
            raise ValueError("pair identity excludes a derived boundary view")
        _require_text(pair_id, "pair ID")
        return f"pair:{pair_id}"
    if view_sha256 is not None:
        _require_sha256(view_sha256, "view hash")
        return f"view:{view_sha256}"
    return f"doc:{doc_id}:{document_sha256}"


def derive_nonce(run_nonce_key: bytes, *, nonce_domain: str,
                 document_view_identity: str, seed: int,
                 worksheet: str) -> str:
    """Derive one catalog D/F nonce without exposing the protected key."""
    if type(run_nonce_key) is not bytes or len(run_nonce_key) < 32:
        raise ValueError("run nonce key must contain at least 32 bytes")
    if (type(nonce_domain) is not str
            or nonce_domain not in {"D1", "D2", "D34", "F", "acceptance-c44"}):
        raise ValueError("unknown nonce domain")
    if (type(seed) is not int or seed not in {1, 17, 20260804}
            or type(worksheet) is not str or worksheet not in {"v1", "v2"}):
        raise ValueError("invalid nonce seed or worksheet")
    _require_text(document_view_identity, "document view identity")
    message = canonical_json({
        "document_view_identity": document_view_identity,
        "domain": "c0b2-nonce-v1", "nonce_domain": nonce_domain,
        "seed": seed, "worksheet": worksheet,
    })
    nonce = "FENCE_" + hmac.new(
        run_nonce_key, message, hashlib.sha256).hexdigest()[:32].upper()
    _validate_nonce(nonce)
    return nonce


def derive_health_nonce(run_nonce_key: bytes, *, candidate_id: str,
                        document_view_identity: str, worksheet: str) -> str:
    """Derive the separate, candidate-bound health nonce domain."""
    if type(run_nonce_key) is not bytes or len(run_nonce_key) < 32:
        raise ValueError("run nonce key must contain at least 32 bytes")
    _require_sha256(candidate_id, "candidate ID")
    if type(worksheet) is not str or worksheet not in {"v1", "v2"}:
        raise ValueError("invalid health nonce registry identity")
    _require_text(document_view_identity, "document view identity")
    message = canonical_json({
        "candidate_id": candidate_id,
        "document_view_identity": document_view_identity,
        "domain": "c0b2-health-nonce-v1", "seed": 1,
        "worksheet": worksheet,
    })
    nonce = "FENCE_" + hmac.new(
        run_nonce_key, message, hashlib.sha256).hexdigest()[:32].upper()
    _validate_nonce(nonce)
    return nonce


def canonical_payload(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-compatible object, rejecting non-mapping inputs."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise TypeError("canonical artifact must be a model or mapping")
    return dict(value)


def require_exact(stored: BaseModel | Mapping[str, Any],
                  derived: BaseModel | Mapping[str, Any], *, label: str) -> str:
    """Require byte-identical canonical evidence and return its artifact hash."""
    stored_payload = canonical_payload(stored)
    derived_payload = canonical_payload(derived)
    if canonical_json(stored_payload) != canonical_json(derived_payload):
        raise PublicScoringError(f"{label} differs from independently derived evidence")
    return sha256_json(derived_payload)


def require_hash(value: BaseModel | Mapping[str, Any], expected_sha256: str,
                 *, label: str) -> None:
    """Fail closed when a stored external artifact hash differs."""
    _require_sha256(expected_sha256, f"{label} hash")
    if sha256_json(canonical_payload(value)) != expected_sha256:
        raise PublicScoringError(f"{label} hash differs from canonical artifact")


def _counts(*values: int) -> None:
    if any(type(value) is not int for value in values):
        raise TypeError("counts must be exact integers")
    if any(value < 0 for value in values):
        raise ValueError("counts must be nonnegative")


def _category_set(values: Sequence[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("category row must be a sequence, not a string")
    row = list(values)
    if len(set(row)) != len(row) or any(value not in CATEGORIES for value in row):
        raise ValueError("categories must be unique members of the frozen enum")
    if row != [category for category in CATEGORIES if category in row]:
        raise ValueError("categories must retain frozen order")
    return frozenset(row)


def _answer_findings(worksheet: str,
                     value: Mapping[str, Any]) -> list[dict[str, Any]]:
    if worksheet == "v2":
        return [dict(item) for item in value["findings"]]
    return [
        {"category": row["category"], **dict(item)}
        for row in value["categories"]
        for item in row["evidence"]
    ]


def _require_sha256(value: str, label: str) -> None:
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{label} is not lowercase SHA-256")


def _require_text(value: str, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a nonempty exact string")


def _validate_nonce(value: str) -> None:
    # Reuse the exact annotated catalog type through a tiny strict adapter model.
    class _NonceModel(BaseModel):
        model_config = {"strict": True, "extra": "forbid"}
        nonce: Nonce
    _NonceModel.model_validate({"nonce": value}, strict=True)
