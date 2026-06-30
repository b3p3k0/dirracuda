"""Unit tests for the pure Sherlock grouped-value model (C22).

Covers grouping into visible value rows, comma-input splitting (including the
documented unsupported literal-comma behavior), building individual rows back
from tokens with an injected key factory, key reuse across edits, and flatten
expansion -- plus regressions proving serialization/matcher/export still operate
on individual SherlockPattern rows, never grouped rows.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import shared.sherlock as sherlock_pkg
from shared.sherlock import (
    Severity,
    SherlockPathEntry,
    SherlockPattern,
    SherlockSettings,
    match_entries,
    settings_to_dict,
)
from shared.sherlock.export import build_export_payload
from shared.sherlock.grouping import (
    PatternValueGroup,
    build_group_patterns,
    expand_groups,
    group_patterns,
    reuse_keys,
    split_pattern_input,
)


def _pat(
    pattern,
    *,
    key=None,
    category="Finance",
    label="Tax docs",
    severity=Severity.MED,
    enabled=True,
    builtin=False,
    color_tag="user2",
):
    return SherlockPattern(
        key=key if key is not None else "k_" + pattern,
        category=category,
        label=label,
        pattern=pattern,
        severity=severity,
        enabled=enabled,
        builtin=builtin,
        color_tag=color_tag,
    )


def _counter_mint():
    """A deterministic mint() returning new_1, new_2, ... for test assertions."""
    state = {"n": 0}

    def mint():
        state["n"] += 1
        return "new_{0}".format(state["n"])

    return mint


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def test_same_fields_collapse_to_one_group():
    rows = [_pat("*w2*", key="k1"), _pat("*w4*", key="k2"), _pat("*tax*", key="k3")]
    groups = group_patterns(rows)
    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, PatternValueGroup)
    assert group.patterns == ("*w2*", "*w4*", "*tax*")
    assert group.keys == ("k1", "k2", "k3")
    assert group.label == "Tax docs"
    assert group.severity is Severity.MED
    assert group.color_tag == "user2"


def test_different_category_splits():
    groups = group_patterns([_pat("*w2*", category="Finance"), _pat("*w4*", category="Other")])
    assert len(groups) == 2


def test_different_label_splits():
    groups = group_patterns([_pat("*w2*", label="Tax docs"), _pat("*w4*", label="W forms")])
    assert len(groups) == 2


def test_different_severity_splits():
    groups = group_patterns(
        [_pat("*w2*", severity=Severity.MED), _pat("*w4*", severity=Severity.HIGH)]
    )
    assert len(groups) == 2


def test_different_enabled_splits():
    groups = group_patterns([_pat("*w2*", enabled=True), _pat("*w4*", enabled=False)])
    assert len(groups) == 2


def test_builtin_and_custom_never_merge():
    # Identical in every field except builtin -> must stay in separate groups.
    groups = group_patterns(
        [_pat("*w2*", key="b", builtin=True), _pat("*w2*", key="c", builtin=False)]
    )
    assert len(groups) == 2
    assert {g.builtin for g in groups} == {True, False}


def test_different_color_tag_splits():
    groups = group_patterns([_pat("*w2*", color_tag="user2"), _pat("*w4*", color_tag="user3")])
    assert len(groups) == 2


def test_group_and_member_order_is_first_occurrence():
    a1 = _pat("*w2*", key="a1", category="Finance", label="Tax docs")
    b1 = _pat("*ssn*", key="b1", category="PII", label="SSN")
    a2 = _pat("*w4*", key="a2", category="Finance", label="Tax docs")
    groups = group_patterns([a1, b1, a2])  # interleaved A, B, A
    assert [g.category for g in groups] == ["Finance", "PII"]
    assert groups[0].patterns == ("*w2*", "*w4*")  # members keep first-seen order
    assert groups[1].patterns == ("*ssn*",)


# --------------------------------------------------------------------------- #
# Split (Add/Edit input parsing)
# --------------------------------------------------------------------------- #


def test_split_basic():
    assert split_pattern_input("*w2*,*w4*,*tax*") == ["*w2*", "*w4*", "*tax*"]


def test_split_trims_whitespace():
    assert split_pattern_input("  *w2* , *w4*  ,*tax*") == ["*w2*", "*w4*", "*tax*"]


def test_split_drops_empty_tokens():
    assert split_pattern_input("a,,b, ,c") == ["a", "b", "c"]


def test_split_dedupes_preserving_first_occurrence():
    assert split_pattern_input("a, b, a, c, b") == ["a", "b", "c"]


def test_split_empty_string():
    assert split_pattern_input("") == []
    assert split_pattern_input("   ") == []
    assert split_pattern_input(",, ,") == []


def test_split_literal_comma_in_pattern_unsupported():
    # Documented limitation: a comma inside a pattern is split, not preserved.
    assert split_pattern_input("*a,b*") == ["*a", "b*"]


# --------------------------------------------------------------------------- #
# Expansion -- flatten (expand_groups)
# --------------------------------------------------------------------------- #


def test_expand_groups_returns_individual_rows():
    rows = [_pat("*w2*", key="k1"), _pat("*w4*", key="k2")]
    out = expand_groups(group_patterns(rows))
    assert all(isinstance(r, SherlockPattern) for r in out)
    assert [r.pattern for r in out] == ["*w2*", "*w4*"]


def test_group_then_expand_round_trip_contiguous():
    # Contiguous same-group rows round-trip to the exact same list.
    rows = [
        _pat("*w2*", key="k1"),
        _pat("*w4*", key="k2"),
        _pat("*ssn*", key="k3", category="PII", label="SSN"),
    ]
    assert expand_groups(group_patterns(rows)) == rows


def test_expand_preserves_all_fields():
    src = _pat(
        "*w2*", key="k1", category="Finance", label="Tax docs",
        severity=Severity.HIGH, enabled=False, builtin=True, color_tag="user3",
    )
    out = expand_groups(group_patterns([src]))
    assert out == [src]


# --------------------------------------------------------------------------- #
# Expansion -- row creation (build_group_patterns)
# --------------------------------------------------------------------------- #


def test_build_one_row_per_token_shares_fields():
    rows = build_group_patterns(
        category="Finance",
        label="Tax docs",
        severity=Severity.MED,
        enabled=True,
        builtin=False,
        color_tag="user2",
        patterns=["*w2*", "*w4*", "*tax*"],
        key_for=lambda pattern: "k_" + pattern,
    )
    assert [r.pattern for r in rows] == ["*w2*", "*w4*", "*tax*"]
    assert [r.key for r in rows] == ["k_*w2*", "k_*w4*", "k_*tax*"]
    assert all(r.category == "Finance" and r.label == "Tax docs" for r in rows)
    assert all(r.severity is Severity.MED and r.color_tag == "user2" for r in rows)
    assert all(r.builtin is False and r.enabled is True for r in rows)


def test_build_normalizes_color_tag():
    rows = build_group_patterns(
        category="Finance",
        label="Tax docs",
        severity=Severity.MED,
        enabled=True,
        builtin=False,
        color_tag="USER2",  # non-canonical casing
        patterns=["*w2*"],
        key_for=lambda pattern: "k",
    )
    assert rows[0].color_tag == "user2"


def test_build_empty_tokens():
    rows = build_group_patterns(
        category="Finance",
        label="Tax docs",
        severity=Severity.MED,
        enabled=True,
        builtin=False,
        color_tag="none",
        patterns=[],
        key_for=lambda pattern: "k",
    )
    assert rows == []


def test_build_composes_with_split():
    # The full grouped value row -> individual rows path.
    tokens = split_pattern_input("*w2*, *w4*, *tax*")
    rows = build_group_patterns(
        category="Finance",
        label="Tax docs",
        severity=Severity.MED,
        enabled=True,
        builtin=False,
        color_tag="user2",
        patterns=tokens,
        key_for=lambda pattern: "k_" + pattern,
    )
    assert [r.pattern for r in rows] == ["*w2*", "*w4*", "*tax*"]


# --------------------------------------------------------------------------- #
# Key strategy (reuse_keys)
# --------------------------------------------------------------------------- #


def test_reuse_keys_reuses_match_mints_new():
    key_for = reuse_keys([_pat("*a*", key="ka")], _counter_mint())
    assert key_for("*a*") == "ka"  # reused
    assert key_for("*b*") == "new_1"  # minted


def test_reuse_keys_drop_and_add():
    existing = [_pat("*w2*", key="k1"), _pat("*w4*", key="k2")]
    rows = build_group_patterns(
        category="Finance",
        label="Tax docs",
        severity=Severity.MED,
        enabled=True,
        builtin=False,
        color_tag="user2",
        patterns=["*w2*", "*tax*"],  # keep *w2*, drop *w4*, add *tax*
        key_for=reuse_keys(existing, _counter_mint()),
    )
    assert rows[0].key == "k1"  # surviving pattern reuses its key
    assert rows[1].key == "new_1"  # only the added pattern mints
    assert rows[1].pattern == "*tax*"


def test_reuse_keys_no_op_edit_is_key_stable():
    members = [_pat("*w2*", key="k1"), _pat("*w4*", key="k2"), _pat("*tax*", key="k3")]
    group = group_patterns(members)[0]

    def _no_mint():
        raise AssertionError("mint must not be called for a no-op edit")

    rebuilt = build_group_patterns(
        category=group.category,
        label=group.label,
        severity=group.severity,
        enabled=group.enabled,
        builtin=group.builtin,
        color_tag=group.color_tag,
        patterns=list(group.patterns),
        key_for=reuse_keys(group.members, _no_mint),
    )
    # No keys minted, and the rebuilt rows equal the originals exactly.
    assert rebuilt == list(group.members)


# --------------------------------------------------------------------------- #
# Regressions: downstream still operates on individual rows
# --------------------------------------------------------------------------- #


def test_serialization_writes_individual_rows_not_grouped():
    customs = expand_groups(
        group_patterns([_pat("*w2*", key="c1"), _pat("*w4*", key="c2")])
    )
    data = settings_to_dict(SherlockSettings(patterns=customs))
    assert [c["pattern"] for c in data["custom_patterns"]] == ["*w2*", "*w4*"]
    assert [c["key"] for c in data["custom_patterns"]] == ["c1", "c2"]
    # No grouped/value container leaks into the wire format.
    assert "groups" not in data
    assert "value_rows" not in data


def test_matcher_matches_each_expanded_pattern_independently():
    src = [
        _pat("secret", key="k1", severity=Severity.HIGH),
        _pat("password", key="k2", severity=Severity.HIGH),
        _pat("token", key="k3", severity=Severity.HIGH),
    ]
    rows = expand_groups(group_patterns(src))
    entry = SherlockPathEntry(
        display_path="share/secret_password_token.txt",
        segments=("share", "secret_password_token.txt"),
        container="share",
    )
    result = match_entries([entry], SherlockSettings(ignore_case=True, patterns=rows))
    assert result.hit_count == 3  # each expanded pattern matches independently
    assert result.highest_severity is Severity.HIGH


def test_export_remains_full_catalog_row_based():
    rows = expand_groups(
        group_patterns([_pat("*w2*", key="k1"), _pat("*w4*", key="k2")])
    )
    payload = build_export_payload(rows, exported_at="t")
    assert payload["count"] == 2
    assert len(payload["patterns"]) == 2
    assert [r["pattern"] for r in payload["patterns"]] == ["*w2*", "*w4*"]


def test_grouping_symbols_are_public_exports():
    for name in (
        "PatternValueGroup",
        "group_patterns",
        "split_pattern_input",
        "build_group_patterns",
        "reuse_keys",
        "expand_groups",
    ):
        assert name in sherlock_pkg.__all__
        assert hasattr(sherlock_pkg, name)
