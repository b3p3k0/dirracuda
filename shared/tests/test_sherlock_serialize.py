"""Tests for shared.sherlock.serialize: pure SherlockSettings <-> dict mapping."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.sherlock import (
    DEFAULT_COLORS,
    Severity,
    SherlockPattern,
    SherlockSettings,
    builtin_patterns,
    default_settings,
    settings_from_dict,
    settings_to_dict,
)
from shared.sherlock.serialize import severity_from_str, severity_to_str


def test_round_trip_defaults_is_faithful():
    settings = default_settings()
    restored = settings_from_dict(settings_to_dict(settings))

    assert restored.ignore_case is settings.ignore_case
    assert restored.run_after_probe is settings.run_after_probe
    assert restored.colors == settings.colors
    assert [p.key for p in restored.patterns] == [p.key for p in settings.patterns]
    assert all(p.enabled for p in restored.patterns)


def test_empty_and_garbage_inputs_yield_defaults():
    default_keys = [p.key for p in default_settings().patterns]
    for raw in ({}, None, [], "nope", 7):
        restored = settings_from_dict(raw)
        assert restored.ignore_case is True
        assert restored.run_after_probe is False
        assert restored.colors == DEFAULT_COLORS
        assert [p.key for p in restored.patterns] == default_keys


def test_invalid_color_falls_back_to_default():
    restored = settings_from_dict(
        {"colors": {"high": "red", "med": "#ffa31a", "low": "#zzzzzz"}}
    )
    assert restored.color_for(Severity.HIGH) == DEFAULT_COLORS[Severity.HIGH]
    assert restored.color_for(Severity.MED) == "#ffa31a"
    assert restored.color_for(Severity.LOW) == DEFAULT_COLORS[Severity.LOW]


def test_builtin_disabled_flips_only_named_keys():
    target = builtin_patterns()[0].key
    restored = settings_from_dict({"builtin_disabled": [target]})
    by_key = {p.key: p for p in restored.patterns}
    assert by_key[target].enabled is False
    assert all(p.enabled for k, p in by_key.items() if k != target)


def test_unknown_builtin_disabled_keys_ignored():
    restored = settings_from_dict({"builtin_disabled": ["does_not_exist"]})
    assert all(p.enabled for p in restored.patterns if p.builtin)


def test_new_builtin_appears_enabled_after_load():
    # A settings dict written before a built-in existed simply omits it; the
    # current catalog still surfaces it (enabled) on load.
    catalog_keys = {p.key for p in builtin_patterns()}
    restored = settings_from_dict({"builtin_disabled": []})
    assert {p.key for p in restored.patterns if p.builtin} == catalog_keys


def test_deleted_builtin_round_trip_is_absent_after_reload():
    full = builtin_patterns()
    target = full[0].key
    # Staged catalog with one built-in deleted (absent from the list).
    settings = SherlockSettings(patterns=[p for p in full if p.key != target])
    data = settings_to_dict(settings)
    assert data["builtin_deleted"] == [target]

    restored = settings_from_dict(data)
    keys = {p.key for p in restored.patterns if p.builtin}
    assert target not in keys
    assert keys == {p.key for p in full if p.key != target}


def test_builtin_deleted_absent_is_legacy_parity():
    # A legacy dict without builtin_deleted regenerates the full catalog.
    catalog_keys = {p.key for p in builtin_patterns()}
    restored = settings_from_dict({"builtin_disabled": []})
    assert {p.key for p in restored.patterns if p.builtin} == catalog_keys


def test_unknown_builtin_deleted_keys_ignored():
    catalog_keys = {p.key for p in builtin_patterns()}
    restored = settings_from_dict({"builtin_deleted": ["does_not_exist"]})
    assert {p.key for p in restored.patterns if p.builtin} == catalog_keys


def test_deleted_builtin_preserves_other_state_and_customs():
    full = builtin_patterns()
    deleted = full[0].key
    disabled = full[1].key
    custom = SherlockPattern(
        key="custom_x", category="Custom", label="Acme", pattern="*acme*",
        severity=Severity.HIGH, enabled=True, builtin=False,
    )
    kept = []
    for p in full:
        if p.key == deleted:
            continue
        if p.key == disabled:
            kept.append(
                SherlockPattern(
                    key=p.key, category=p.category, label=p.label,
                    pattern=p.pattern, severity=p.severity, enabled=False,
                    builtin=True,
                )
            )
        else:
            kept.append(p)
    settings = SherlockSettings(patterns=kept + [custom])

    restored = settings_from_dict(settings_to_dict(settings))
    by_key = {p.key: p for p in restored.patterns}
    assert deleted not in by_key
    assert by_key[disabled].enabled is False
    assert by_key["custom_x"].pattern == "*acme*"


def test_custom_patterns_round_trip():
    settings = SherlockSettings(
        patterns=builtin_patterns()
        + [
            SherlockPattern(
                key="custom_x",
                category="Custom",
                label="Acme",
                pattern="*acme*",
                severity=Severity.HIGH,
                enabled=True,
                builtin=False,
            )
        ]
    )
    restored = settings_from_dict(settings_to_dict(settings))
    customs = [p for p in restored.patterns if not p.builtin]
    assert len(customs) == 1
    assert customs[0].key == "custom_x"
    assert customs[0].pattern == "*acme*"
    assert customs[0].severity is Severity.HIGH


def test_malformed_custom_rows_are_dropped():
    restored = settings_from_dict(
        {
            "custom_patterns": [
                {"key": "ok", "pattern": "*a*", "severity": "low"},
                {"key": "no_pattern"},
                {"key": "blank", "pattern": "   "},
                {"pattern": "*b*"},  # missing key
                "not_a_dict",
            ]
        }
    )
    customs = [p for p in restored.patterns if not p.builtin]
    assert [p.key for p in customs] == ["ok"]


def test_severity_token_mapping_both_directions():
    for sev in (Severity.HIGH, Severity.MED, Severity.LOW):
        assert severity_from_str(severity_to_str(sev)) is sev
    assert severity_from_str("HIGH") is Severity.HIGH
    assert severity_from_str("bogus") is Severity.MED


def test_to_dict_only_records_disabled_builtins():
    patterns = builtin_patterns()
    disabled_key = patterns[2].key
    patterns[2] = SherlockPattern(
        key=patterns[2].key,
        category=patterns[2].category,
        label=patterns[2].label,
        pattern=patterns[2].pattern,
        severity=patterns[2].severity,
        enabled=False,
        builtin=True,
    )
    data = settings_to_dict(SherlockSettings(patterns=patterns))
    assert data["builtin_disabled"] == [disabled_key]
    assert data["custom_patterns"] == []


# --- V2 user color tags (C8) ---


def test_user_colors_and_color_tag_round_trip():
    settings = SherlockSettings(
        user_colors={"user1": "#abcdef", "user2": "", "user3": "#001122"},
        patterns=builtin_patterns()
        + [
            SherlockPattern(
                key="custom_tagged",
                category="Custom",
                label="Tagged",
                pattern="*acme*",
                severity=Severity.MED,
                enabled=True,
                builtin=False,
                color_tag="user1",
            )
        ],
    )
    restored = settings_from_dict(settings_to_dict(settings))
    assert restored.user_colors == {"user1": "#abcdef", "user2": "", "user3": "#001122"}
    custom = [p for p in restored.patterns if not p.builtin][0]
    assert custom.color_tag == "user1"


def test_v1_dict_loads_with_empty_user_colors_and_untagged_customs():
    restored = settings_from_dict(
        {"custom_patterns": [{"key": "c", "pattern": "*x*", "severity": "low"}]}
    )
    assert restored.user_colors == {"user1": "", "user2": "", "user3": ""}
    custom = [p for p in restored.patterns if not p.builtin][0]
    assert custom.color_tag == "none"


def test_invalid_user_color_degrades_to_empty():
    restored = settings_from_dict(
        {"user_colors": {"user1": "red", "user2": "#ABCDEF", "user3": 7}}
    )
    assert restored.user_colors == {"user1": "", "user2": "#abcdef", "user3": ""}


def test_unknown_color_tag_token_degrades_to_none():
    restored = settings_from_dict(
        {"custom_patterns": [{"key": "c", "pattern": "*x*", "color_tag": "user9"}]}
    )
    custom = [p for p in restored.patterns if not p.builtin][0]
    assert custom.color_tag == "none"


def test_builtins_never_serialize_a_color_tag():
    patterns = builtin_patterns()
    disabled_key = patterns[1].key
    patterns[1] = SherlockPattern(
        key=patterns[1].key,
        category=patterns[1].category,
        label=patterns[1].label,
        pattern=patterns[1].pattern,
        severity=patterns[1].severity,
        enabled=False,
        builtin=True,
    )
    data = settings_to_dict(SherlockSettings(patterns=patterns))
    # Built-ins persist only by disabled key — never as custom rows, so no
    # built-in color_tag is ever written to state.
    assert data["builtin_disabled"] == [disabled_key]
    assert data["custom_patterns"] == []
