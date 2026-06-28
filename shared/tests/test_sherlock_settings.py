"""Unit tests for Sherlock settings, colors, and the built-in catalog."""

import pytest

from shared.sherlock import (
    COLOR_TAG_NONE,
    DEFAULT_COLORS,
    Severity,
    SherlockSettings,
    builtin_patterns,
    default_settings,
    is_valid_color,
    is_valid_user_color,
    normalize_color_tag,
    validate_color,
    validate_user_color,
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


# --- V2 user color tags (C8) ---


@pytest.mark.parametrize("value", ["", "#ff4d4d", "#FFA31A", "#000000"])
def test_is_valid_user_color_accepts(value):
    assert is_valid_user_color(value) is True


@pytest.mark.parametrize("value", ["#fff", "red", "ff4d4d", None, 123, "#ff4d4dd"])
def test_is_valid_user_color_rejects(value):
    assert is_valid_user_color(value) is False


def test_validate_user_color_empty_passes_through():
    assert validate_user_color("") == ""


def test_validate_user_color_normalizes_lowercase():
    assert validate_user_color("#FFA31A") == "#ffa31a"


def test_validate_user_color_raises_on_invalid():
    with pytest.raises(ValueError):
        validate_user_color("not-a-color")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("user1", "user1"),
        ("USER2 ", "user2"),
        (" User3", "user3"),
        ("none", "none"),
        ("", "none"),
        (None, "none"),
        ("user9", "none"),
        (7, "none"),
    ],
)
def test_normalize_color_tag(value, expected):
    assert normalize_color_tag(value) == expected


def test_default_settings_user_colors_empty():
    assert default_settings().user_colors == {"user1": "", "user2": "", "user3": ""}


def test_user_color_for_returns_configured_or_empty():
    settings = SherlockSettings(user_colors={"user1": "#abcdef", "user2": "", "user3": ""})
    assert settings.user_color_for("user1") == "#abcdef"
    assert settings.user_color_for("USER1") == "#abcdef"
    assert settings.user_color_for("user2") == ""
    assert settings.user_color_for("none") == ""
    assert settings.user_color_for("user9") == ""


def test_builtins_are_always_untagged():
    assert all(p.color_tag == COLOR_TAG_NONE for p in builtin_patterns())


def test_tint_for_user_color_wins_when_configured():
    settings = SherlockSettings(
        colors={Severity.HIGH: "#010203", Severity.MED: "#040506", Severity.LOW: "#070809"},
        user_colors={"user1": "#abcdef", "user2": "", "user3": ""},
    )
    # Configured user color wins over the severity color.
    assert settings.tint_for(Severity.HIGH, "user1") == "#abcdef"


def test_tint_for_falls_back_to_severity():
    settings = SherlockSettings(
        colors={Severity.HIGH: "#010203", Severity.MED: "#040506", Severity.LOW: "#070809"},
        user_colors={"user1": "", "user2": "", "user3": ""},
    )
    # Empty user color, 'none', and unknown tokens all fall back to severity.
    assert settings.tint_for(Severity.HIGH, "user1") == "#010203"
    assert settings.tint_for(Severity.MED, "none") == "#040506"
    assert settings.tint_for(Severity.LOW, "user9") == "#070809"
    assert settings.tint_for(Severity.LOW, None) == "#070809"


def test_tint_for_returns_empty_when_no_severity_and_no_user_color():
    settings = default_settings()
    assert settings.tint_for(None, None) == ""
    assert settings.tint_for(None, "none") == ""
