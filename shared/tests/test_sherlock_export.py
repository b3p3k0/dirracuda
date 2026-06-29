"""Unit tests for the pure Sherlock export payload builder (C19)."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sherlock import Severity, SherlockPattern, builtin_patterns
from shared.sherlock.export import (
    EXPORT_SCHEMA_NAME,
    EXPORT_SCHEMA_VERSION,
    build_export_payload,
)


def _custom(**kw) -> SherlockPattern:
    base = dict(
        key="custom_1",
        category="Secrets",
        label="My secret",
        pattern="*secret*",
        severity=Severity.HIGH,
        enabled=True,
        builtin=False,
        color_tag="user2",
    )
    base.update(kw)
    return SherlockPattern(**base)


def test_payload_metadata_shape():
    patterns = builtin_patterns()
    payload = build_export_payload(patterns, exported_at="2026-06-29T12:00:00")
    assert payload["schema"] == EXPORT_SCHEMA_NAME
    assert payload["schema_version"] == EXPORT_SCHEMA_VERSION
    assert payload["exported_at"] == "2026-06-29T12:00:00"
    assert payload["count"] == len(patterns)
    assert len(payload["patterns"]) == len(patterns)


def test_builtin_row_fields():
    builtin = builtin_patterns()[0]
    row = build_export_payload([builtin], exported_at="t")["patterns"][0]
    assert row["type"] == "builtin"
    assert row["key"] == builtin.key
    assert row["category"] == builtin.category
    assert row["label"] == builtin.label
    assert row["pattern"] == builtin.pattern
    assert row["enabled"] is True
    assert row["severity"] in {"high", "med", "low"}
    assert row["color_tag"] == "none"


def test_custom_row_fields():
    row = build_export_payload([_custom()], exported_at="t")["patterns"][0]
    assert row["type"] == "custom"
    assert row["key"] == "custom_1"
    assert row["severity"] == "high"
    assert row["color_tag"] == "user2"
    assert row["enabled"] is True


def test_severity_serialized_to_tokens():
    rows = build_export_payload(
        [
            _custom(key="h", severity=Severity.HIGH),
            _custom(key="m", severity=Severity.MED),
            _custom(key="l", severity=Severity.LOW),
        ],
        exported_at="t",
    )["patterns"]
    assert [r["severity"] for r in rows] == ["high", "med", "low"]


def test_color_tag_normalized():
    row = build_export_payload(
        [_custom(color_tag="BOGUS")], exported_at="t"
    )["patterns"][0]
    assert row["color_tag"] == "none"


def test_disabled_flag_preserved():
    row = build_export_payload(
        [_custom(enabled=False)], exported_at="t"
    )["patterns"][0]
    assert row["enabled"] is False


def test_empty_input():
    payload = build_export_payload([], exported_at="t")
    assert payload["count"] == 0
    assert payload["patterns"] == []


def test_row_order_preserved():
    patterns = [
        _custom(key="a"),
        _custom(key="b"),
        _custom(key="c"),
    ]
    rows = build_export_payload(patterns, exported_at="t")["patterns"]
    assert [r["key"] for r in rows] == ["a", "b", "c"]


def test_input_not_mutated():
    patterns = builtin_patterns()
    before = list(patterns)
    build_export_payload(patterns, exported_at="t")
    assert patterns == before
    assert len(patterns) == len(before)
