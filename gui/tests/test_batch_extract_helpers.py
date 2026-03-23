"""
Tests for gui/utils/batch_extract_helpers.py

All tests are pure-Python; no Tkinter import or display required.

Dual-semantics contract (load_extension_filters normalize flag):
  normalize=True  — normalizes values, injects NO_EXTENSION_TOKEN when absent
  normalize=False — raw load, no injection (caller uses ensure_no_extension_token)
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.batch_extract_helpers import (
    NO_EXTENSION_TOKEN,
    ensure_no_extension_token,
    load_extension_filters,
    normalize_loaded_extensions,
    sort_extensions,
    validate_extension,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config_with_extensions(tmp_path):
    """Return a factory that writes a config.json and returns its Path."""
    def _make(included=None, excluded=None):
        data = {
            "file_collection": {
                "included_extensions": included if included is not None else [],
                "excluded_extensions": excluded if excluded is not None else [],
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p
    return _make


# ---------------------------------------------------------------------------
# NO_EXTENSION_TOKEN constant
# ---------------------------------------------------------------------------

def test_no_extension_token_value():
    assert NO_EXTENSION_TOKEN == "<no extension>"


# ---------------------------------------------------------------------------
# normalize_loaded_extensions
# ---------------------------------------------------------------------------

def test_normalize_lowercases():
    assert normalize_loaded_extensions([".TXT", ".PDF"]) == [".txt", ".pdf"]


def test_normalize_deduplicates():
    result = normalize_loaded_extensions([".txt", ".TXT", ".txt"])
    assert result == [".txt"]


def test_normalize_maps_blank_to_token():
    result = normalize_loaded_extensions(["", "  "])
    assert result == [NO_EXTENSION_TOKEN]


def test_normalize_skips_non_strings():
    result = normalize_loaded_extensions([None, 42, ".py"])
    assert result == [".py"]


def test_normalize_empty_input():
    assert normalize_loaded_extensions([]) == []
    assert normalize_loaded_extensions(None) == []


# ---------------------------------------------------------------------------
# load_extension_filters — normalize=True (BatchExtractSettingsDialog path)
# ---------------------------------------------------------------------------

def test_load_normalize_true_injects_token_when_absent(config_with_extensions):
    """Token must be injected into included_extensions when absent from both lists."""
    path = config_with_extensions(included=[".txt"], excluded=[".exe"])
    result = load_extension_filters(path, normalize=True)
    assert NO_EXTENSION_TOKEN in result["included_extensions"]


def test_load_normalize_true_no_duplicate_token_in_included(config_with_extensions):
    """Token already in included_extensions — must not be duplicated."""
    path = config_with_extensions(
        included=[NO_EXTENSION_TOKEN, ".txt"],
        excluded=[],
    )
    result = load_extension_filters(path, normalize=True)
    token_count = sum(1 for e in result["included_extensions"] if e == NO_EXTENSION_TOKEN)
    assert token_count == 1


def test_load_normalize_true_no_injection_when_token_in_excluded(config_with_extensions):
    """Token in excluded_extensions — must not be added to included_extensions."""
    path = config_with_extensions(
        included=[".txt"],
        excluded=[NO_EXTENSION_TOKEN],
    )
    result = load_extension_filters(path, normalize=True)
    assert NO_EXTENSION_TOKEN not in result["included_extensions"]


def test_load_normalize_true_normalizes_values(config_with_extensions):
    """Values must be lowercased and deduplicated when normalize=True."""
    path = config_with_extensions(included=[".TXT", ".TXT"], excluded=[])
    result = load_extension_filters(path, normalize=True)
    assert result["included_extensions"].count(".txt") == 1


# ---------------------------------------------------------------------------
# load_extension_filters — normalize=False (ExtensionEditorDialog path)
# ---------------------------------------------------------------------------

def test_load_normalize_false_does_not_inject_token(config_with_extensions):
    """normalize=False must return raw values without injecting the token."""
    path = config_with_extensions(included=[".txt"], excluded=[".exe"])
    result = load_extension_filters(path, normalize=False)
    assert NO_EXTENSION_TOKEN not in result["included_extensions"]
    assert NO_EXTENSION_TOKEN not in result["excluded_extensions"]


def test_load_normalize_false_preserves_raw_case(config_with_extensions):
    """normalize=False must return values exactly as stored (no lowercasing)."""
    path = config_with_extensions(included=[".TXT", ".PDF"], excluded=[])
    result = load_extension_filters(path, normalize=False)
    assert result["included_extensions"] == [".TXT", ".PDF"]


def test_load_normalize_false_empty_config(config_with_extensions):
    path = config_with_extensions(included=[], excluded=[])
    result = load_extension_filters(path, normalize=False)
    assert result == {"included_extensions": [], "excluded_extensions": []}


def test_load_missing_config_returns_empty_defaults(tmp_path):
    missing = tmp_path / "nonexistent.json"
    for flag in (True, False):
        result = load_extension_filters(missing, normalize=flag)
        assert result["included_extensions"] == [] or (
            flag and result["included_extensions"] == [NO_EXTENSION_TOKEN]
        )


# ---------------------------------------------------------------------------
# normalize=False + ensure_no_extension_token ≡ normalize=True (token presence)
# ---------------------------------------------------------------------------

def test_load_normalize_false_then_ensure_token_matches_normalize_true(config_with_extensions):
    """
    Calling load_extension_filters(normalize=False) followed by
    ensure_no_extension_token() must produce the same token-presence outcome
    as load_extension_filters(normalize=True).
    """
    path = config_with_extensions(included=[".txt"], excluded=[".exe"])

    result_true = load_extension_filters(path, normalize=True)

    result_false = load_extension_filters(path, normalize=False)
    ensure_no_extension_token(
        result_false["included_extensions"],
        result_false["excluded_extensions"],
    )

    token_in_true = NO_EXTENSION_TOKEN in (
        result_true["included_extensions"] + result_true["excluded_extensions"]
    )
    token_in_false = NO_EXTENSION_TOKEN in (
        result_false["included_extensions"] + result_false["excluded_extensions"]
    )
    assert token_in_true == token_in_false


# ---------------------------------------------------------------------------
# ensure_no_extension_token
# ---------------------------------------------------------------------------

def test_ensure_no_extension_token_adds_to_included():
    included: list = []
    excluded: list = []
    ensure_no_extension_token(included, excluded)
    assert NO_EXTENSION_TOKEN in included


def test_ensure_no_extension_token_noop_if_in_included():
    included = [NO_EXTENSION_TOKEN, ".txt"]
    excluded: list = []
    ensure_no_extension_token(included, excluded)
    count = included.count(NO_EXTENSION_TOKEN)
    assert count == 1


def test_ensure_no_extension_token_noop_if_in_excluded():
    included: list = [".txt"]
    excluded = [NO_EXTENSION_TOKEN]
    ensure_no_extension_token(included, excluded)
    assert NO_EXTENSION_TOKEN not in included


def test_ensure_no_extension_token_inserts_at_index_zero():
    included = [".txt", ".pdf"]
    ensure_no_extension_token(included, [])
    assert included[0] == NO_EXTENSION_TOKEN


# ---------------------------------------------------------------------------
# validate_extension
# ---------------------------------------------------------------------------

def test_validate_valid_extension():
    ok, norm, err = validate_extension(".txt", [], [])
    assert ok is True
    assert norm == ".txt"
    assert err == ""


def test_validate_adds_leading_dot():
    ok, norm, err = validate_extension("txt", [], [])
    assert ok is True
    assert norm == ".txt"


def test_validate_lowercases():
    ok, norm, err = validate_extension(".TXT", [], [])
    assert norm == ".txt"


def test_validate_empty_returns_error():
    ok, norm, err = validate_extension("", [], [])
    assert ok is False
    assert err != ""


def test_validate_duplicate_in_source():
    ok, norm, err = validate_extension(".txt", [".txt"], [])
    assert ok is False


def test_validate_duplicate_in_other():
    ok, norm, err = validate_extension(".txt", [], [".txt"])
    assert ok is False


def test_validate_unsafe_chars():
    ok, norm, err = validate_extension(".tx/t", [], [])
    assert ok is False


def test_validate_no_extension_token_accepted():
    ok, norm, err = validate_extension(NO_EXTENSION_TOKEN, [], [])
    assert ok is True
    assert norm == NO_EXTENSION_TOKEN


def test_validate_no_extension_token_duplicate():
    ok, norm, err = validate_extension(NO_EXTENSION_TOKEN, [NO_EXTENSION_TOKEN], [])
    assert ok is False


# ---------------------------------------------------------------------------
# sort_extensions
# ---------------------------------------------------------------------------

def test_sort_extensions_alphabetical():
    exts = [".zip", ".abc", ".txt"]
    result = sort_extensions(exts)
    assert result == [".abc", ".txt", ".zip"]


def test_sort_extensions_pins_token_at_top():
    exts = [".zip", NO_EXTENSION_TOKEN, ".abc"]
    result = sort_extensions(exts)
    assert result[0] == NO_EXTENSION_TOKEN
    assert result[1:] == [".abc", ".zip"]


def test_sort_extensions_deduplicates():
    exts = [".txt", ".TXT", ".txt"]
    result = sort_extensions(exts)
    assert len(result) == 1


def test_sort_extensions_mutates_in_place():
    exts = [".z", ".a"]
    result = sort_extensions(exts)
    assert exts is not result  # different list object but...
    assert exts == result      # ...same contents after mutation
