"""Unit tests for the pure Sherlock matcher."""

import pytest

from shared.sherlock import (
    Severity,
    SherlockPathEntry,
    SherlockPattern,
    SherlockSettings,
    match_entries,
)


def _settings(patterns, ignore_case=True):
    return SherlockSettings(ignore_case=ignore_case, patterns=list(patterns))


def _entry(display_path, segments=None, container=None):
    if segments is None:
        segments = tuple(seg for seg in display_path.split("/") if seg)
    return SherlockPathEntry(
        display_path=display_path, segments=tuple(segments), container=container
    )


def _pat(pattern, severity=Severity.HIGH, enabled=True, key="k", color_tag="none"):
    return SherlockPattern(
        key=key,
        category="cat",
        label="label",
        pattern=pattern,
        severity=severity,
        enabled=enabled,
        color_tag=color_tag,
    )


def test_plain_substring_match():
    result = match_entries([_entry("share/secret_notes.txt")], _settings([_pat("secret")]))
    assert result.hit_count == 1
    assert result.highest_severity is Severity.HIGH


def test_plain_substring_no_match():
    result = match_entries([_entry("share/readme.txt")], _settings([_pat("secret")]))
    assert result.hit_count == 0
    assert result.highest_severity is None


def test_wildcard_match():
    result = match_entries([_entry("share/admin.pem")], _settings([_pat("*.pem")]))
    assert result.hit_count == 1


def test_wildcard_question_mark():
    settings = _settings([_pat("key?.txt")])
    assert match_entries([_entry("share/key1.txt")], settings).hit_count == 1
    assert match_entries([_entry("share/key12.txt")], settings).hit_count == 0


def test_case_insensitive_default():
    result = match_entries([_entry("share/PASSWORD.txt")], _settings([_pat("*password*")]))
    assert result.hit_count == 1


def test_case_sensitive_mode():
    settings = _settings([_pat("*password*")], ignore_case=False)
    assert match_entries([_entry("share/PASSWORD.txt")], settings).hit_count == 0
    assert match_entries([_entry("share/password.txt")], settings).hit_count == 1


def test_disabled_patterns_ignored():
    settings = _settings([_pat("secret", enabled=False)])
    assert match_entries([_entry("share/secret.txt")], settings).hit_count == 0


def test_empty_pattern_ignored():
    settings = _settings([_pat("")])
    assert match_entries([_entry("share/anything.txt")], settings).hit_count == 0


def test_segment_match():
    entry = _entry("share/Finance/q1.xlsx", segments=("share", "Finance", "q1.xlsx"))
    result = match_entries([entry], _settings([_pat("Finance")]))
    assert result.hit_count == 1


def test_container_match():
    entry = SherlockPathEntry(
        display_path="reports/file.txt",
        segments=("reports", "file.txt"),
        container="payroll_share",
    )
    result = match_entries([entry], _settings([_pat("*payroll*")]))
    assert result.hit_count == 1


def test_highest_severity_precedence():
    patterns = [
        _pat("*internal*", severity=Severity.LOW, key="low"),
        _pat("*payroll*", severity=Severity.MED, key="med"),
        _pat("*password*", severity=Severity.HIGH, key="high"),
    ]
    entry = _entry("share/internal_payroll_password.txt")
    result = match_entries([entry], _settings(patterns))
    assert result.hit_count == 3
    assert result.highest_severity is Severity.HIGH


def test_hit_count_one_per_entry_pattern_pair():
    # Pattern matches both the full path candidate and a segment, but counts once.
    entry = _entry("secret/secret.txt", segments=("secret", "secret.txt"))
    result = match_entries([entry], _settings([_pat("secret")]))
    assert result.hit_count == 1


def test_display_casing_preserved_in_hit():
    entry = _entry("Share/Secret_Notes.TXT")
    result = match_entries([entry], _settings([_pat("secret")]))
    assert result.hits[0].display_path == "Share/Secret_Notes.TXT"


def test_bracket_neutralization_literal_only():
    # file[ab].txt must match the literal bracket string, not filea.txt/fileb.txt.
    settings = _settings([_pat("file[ab].txt")])
    assert match_entries([_entry("share/file[ab].txt")], settings).hit_count == 1
    assert match_entries([_entry("share/filea.txt")], settings).hit_count == 0
    assert match_entries([_entry("share/fileb.txt")], settings).hit_count == 0


def test_bracket_neutralization_with_wildcards():
    # Combined with * the brackets stay literal (guards chained-replace corruption).
    settings = _settings([_pat("*[ab]*")])
    assert match_entries([_entry("share/x[ab]y.txt")], settings).hit_count == 1
    assert match_entries([_entry("share/xay.txt")], settings).hit_count == 0


# --- V2 color tags (C8) ---


def test_hit_carries_pattern_color_tag():
    result = match_entries(
        [_entry("share/secret.txt")], _settings([_pat("secret", color_tag="user2")])
    )
    assert result.hits[0].color_tag == "user2"


def test_untagged_pattern_hit_is_none_tag():
    result = match_entries([_entry("share/secret.txt")], _settings([_pat("secret")]))
    assert result.hits[0].color_tag == "none"


@pytest.mark.parametrize(
    "raw_tag,expected",
    [("USER1", "user1"), ("user9", "none"), (None, "none")],
)
def test_hit_color_tag_normalized_at_emission(raw_tag, expected):
    # Patterns built manually / by a future UI may carry unnormalized tags; the
    # matcher must still emit stable tokens (uppercase folds, unknown->none).
    result = match_entries(
        [_entry("share/secret.txt")], _settings([_pat("secret", color_tag=raw_tag)])
    )
    assert result.hits[0].color_tag == expected


def test_color_tags_do_not_change_severity_or_count():
    patterns = [
        _pat("*internal*", severity=Severity.LOW, key="low", color_tag="user1"),
        _pat("*payroll*", severity=Severity.MED, key="med", color_tag="user2"),
        _pat("*password*", severity=Severity.HIGH, key="high", color_tag="user3"),
    ]
    entry = _entry("share/internal_payroll_password.txt")
    tagged = match_entries([entry], _settings(patterns))

    baseline_patterns = [
        _pat("*internal*", severity=Severity.LOW, key="low"),
        _pat("*payroll*", severity=Severity.MED, key="med"),
        _pat("*password*", severity=Severity.HIGH, key="high"),
    ]
    baseline = match_entries([entry], _settings(baseline_patterns))

    assert tagged.hit_count == baseline.hit_count == 3
    assert tagged.highest_severity is baseline.highest_severity is Severity.HIGH
