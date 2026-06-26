"""Unit tests for Sherlock settings, colors, and the built-in catalog."""

import pytest

from shared.sherlock import (
    DEFAULT_COLORS,
    Severity,
    builtin_patterns,
    default_settings,
    is_valid_color,
    validate_color,
)


@pytest.mark.parametrize(
    "value",
    ["#ff4d4d", "#FFA31A", "#000000", "#abcdef", "#ABCDEF"],
)
def test_is_valid_color_accepts(value):
    assert is_valid_color(value) is True


@pytest.mark.parametrize(
    "value",
    ["ff4d4d", "#fff", "#gggggg", "#ff4d4d ", "#ff4d4dd", "", None, 123, "#12345"],
)
def test_is_valid_color_rejects(value):
    assert is_valid_color(value) is False


def test_validate_color_normalizes_lowercase():
    assert validate_color("#FFA31A") == "#ffa31a"


def test_validate_color_raises_on_invalid():
    with pytest.raises(ValueError):
        validate_color("not-a-color")


def test_default_colors_match_spec():
    assert DEFAULT_COLORS[Severity.HIGH] == "#ff4d4d"
    assert DEFAULT_COLORS[Severity.MED] == "#ffa31a"
    assert DEFAULT_COLORS[Severity.LOW] == "#ffff80"


def test_default_settings_seeds_enabled_builtins():
    settings = default_settings()
    assert settings.ignore_case is True
    assert settings.run_after_probe is False
    assert all(p.enabled and p.builtin for p in settings.patterns)


def test_builtins_cover_all_seven_groups():
    categories = {p.category for p in builtin_patterns()}
    # Seven SPEC groups collapse into these category labels.
    expected = {
        "Credentials",
        "Private keys",
        "Certificates",
        "PII",
        "Finance",
        "HR/Legal",
        "Customer data",
        "Backups",
        "Database dumps",
        "Internal",
    }
    assert expected.issubset(categories)


def test_builtin_patterns_returns_fresh_objects():
    first = builtin_patterns()
    second = builtin_patterns()
    assert first is not second
    assert all(a is not b for a, b in zip(first, second))


def test_mutating_settings_patterns_does_not_affect_fresh_defaults():
    settings = default_settings()
    original_count = len(default_settings().patterns)
    settings.patterns.clear()
    settings.patterns.append(builtin_patterns()[0])
    assert len(default_settings().patterns) == original_count


def test_severity_precedence_and_display_text():
    assert Severity.HIGH > Severity.MED > Severity.LOW
    assert Severity.HIGH.display_text(3) == "HIGH 3"
    assert Severity.MED.display_name == "MED"
