"""
C4 Server List Risk-column display tests.

Verifies the alert-only contract (HIGH/MED/LOW n text + tint for fresh findings,
blank everywhere else), row tags driven by saved severity colors, and that a
malformed severity token renders blank without crashing the refresh.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tkinter as tk

from gui.components.server_list_window import table
from shared.sherlock import Severity, SherlockSettings, settings_to_dict


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for Tk")
    root.withdraw()
    yield root
    root.destroy()


def _make_tree(tk_root):
    frame, tree, _sv, _sh = table.create_server_table(tk_root, MagicMock(), {})
    return tree


def _tag_bg(tree, tag):
    """Read a tag's background, tolerating Tk returning a str or option tuple."""
    value = tree.tag_configure(tag)["background"]
    return value[-1] if isinstance(value, tuple) else value


def _server(row_key, ip, risk=None, host_type="S"):
    s = {
        "row_key": row_key,
        "ip_address": ip,
        "host_type": host_type,
        "accessible_shares": 0,
        "accessible_shares_list": "",
        "last_seen": "Never",
        "country": "US",
        "favorite": 0,
        "avoid": 0,
    }
    if risk is not None:
        s["sherlock_risk"] = risk
    return s


def test_risk_column_between_extracted_and_type(tk_root):
    tree = _make_tree(tk_root)
    cols = list(tree["columns"])
    assert "Risk" in cols
    assert cols.index("Risk") == cols.index("extracted") + 1
    assert cols.index("Risk") + 1 == cols.index("Type")


def test_finding_shows_text_and_tag(tk_root):
    tree = _make_tree(tk_root)
    servers = [_server("S:1", "1.1.1.1", {"severity": "high", "count": 3, "stale": False})]
    table.update_table_display(tree, servers, None)
    assert tree.set("S:1", "Risk") == "HIGH 3"
    assert "sherlock_high_none" in tree.item("S:1", "tags")


@pytest.mark.parametrize("risk", [
    None,                                                  # never scanned
    {"severity": "high", "count": 5, "stale": True},       # stale
    {"severity": None, "count": 0, "stale": False},        # fresh zero-hit / clear
    {"severity": "high", "count": 0, "stale": False},      # zero count
])
def test_blank_cases_show_no_text_and_no_tag(tk_root, risk):
    tree = _make_tree(tk_root)
    table.update_table_display(tree, [_server("S:1", "1.1.1.1", risk)], None)
    assert tree.set("S:1", "Risk") == ""
    assert not tree.item("S:1", "tags")


@pytest.mark.parametrize("bad", [
    {"severity": "bogus", "count": 4, "stale": False},
    {"severity": None, "count": 4, "stale": False},
    {"severity": "high", "count": "x", "stale": False},
    "not-a-dict",
])
def test_malformed_severity_renders_blank_without_crash(tk_root, bad):
    tree = _make_tree(tk_root)
    table.update_table_display(tree, [_server("S:1", "1.1.1.1", bad)], None)
    assert tree.set("S:1", "Risk") == ""
    assert not tree.item("S:1", "tags")


def test_custom_colors_applied_to_tags(tk_root):
    tree = _make_tree(tk_root)
    settings = SherlockSettings(
        colors={Severity.HIGH: "#010203", Severity.MED: "#040506", Severity.LOW: "#070809"},
    )
    sm = MagicMock()
    sm.get_setting.return_value = settings_to_dict(settings)
    table.update_table_display(
        tree,
        [
            _server("S:1", "1.1.1.1", {"severity": "high", "count": 3, "stale": False}),
            _server("S:2", "1.1.1.2", {"severity": "med", "count": 2, "stale": False}),
        ],
        sm,
    )
    assert _tag_bg(tree, "sherlock_high_none") == "#010203"
    assert _tag_bg(tree, "sherlock_med_none") == "#040506"
    assert tree.set("S:2", "Risk") == "MED 2"
    assert "sherlock_med_none" in tree.item("S:2", "tags")


def test_default_colors_when_no_settings_manager(tk_root):
    tree = _make_tree(tk_root)
    table.update_table_display(
        tree, [_server("S:1", "1.1.1.1", {"severity": "high", "count": 1, "stale": False})], None
    )
    assert _tag_bg(tree, "sherlock_high_none") == "#ff4d4d"


def test_user_color_tint_wins_over_severity(tk_root):
    tree = _make_tree(tk_root)
    settings = SherlockSettings(user_colors={"user1": "#abcdef", "user2": "", "user3": ""})
    sm = MagicMock()
    sm.get_setting.return_value = settings_to_dict(settings)
    risk = {"severity": "high", "count": 3, "stale": False, "display_color_tag": "user1"}
    table.update_table_display(tree, [_server("S:1", "1.1.1.1", risk)], sm)
    # Risk text unchanged; row tinted with the configured user color, not severity.
    assert tree.set("S:1", "Risk") == "HIGH 3"
    assert "sherlock_high_user1" in tree.item("S:1", "tags")
    assert _tag_bg(tree, "sherlock_high_user1") == "#abcdef"


def test_empty_user_color_falls_back_to_severity(tk_root):
    tree = _make_tree(tk_root)
    # user1 unconfigured (empty) -> severity color is used for the tint.
    risk = {"severity": "high", "count": 2, "stale": False, "display_color_tag": "user1"}
    table.update_table_display(tree, [_server("S:1", "1.1.1.1", risk)], None)
    assert tree.set("S:1", "Risk") == "HIGH 2"
    assert "sherlock_high_user1" in tree.item("S:1", "tags")
    assert _tag_bg(tree, "sherlock_high_user1") == "#ff4d4d"
