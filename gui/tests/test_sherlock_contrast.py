"""Sherlock foreground contrast and real-Tk table/theme integration."""

import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock

import pytest

from gui.components import batch_summary_dialog as summary
from gui.components.server_list_window import table
from gui.utils.sherlock_risk_display import sherlock_foreground
from gui.utils.style import SMBSeekTheme
from shared.sherlock import SherlockSettings


@pytest.mark.parametrize("background, expected", [
    ("#ff4d4d", "#111111"),  # default high
    ("#ffa31a", "#111111"),  # default med
    ("#ffff80", "#111111"),  # default low
    ("#00FF00", "#111111"),  # bright custom color, uppercase accepted
    ("#123456", "#ffffff"),
    ("#000000", "#ffffff"),
    ("#ffffff", "#111111"),
    ("#767676", "#ffffff"),  # 4.54:1 with white
    ("#777777", "#000000"),  # white 4.48:1; near-black 4.22:1
    ("#7b7b7b", "#000000"),
    ("#7c7c7c", "#111111"),  # near-black reaches 4.52:1
])
def test_foreground_for_highlights(background, expected):
    assert sherlock_foreground(background) == expected


@pytest.mark.parametrize("invalid", [None, 123, {}, "", "red", "#fff", "#gg0000", "#11223344"])
def test_foreground_rejects_invalid_colors(invalid):
    with pytest.raises(ValueError, match="expected #RRGGBB"):
        sherlock_foreground(invalid)


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def _trees(widget):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Treeview):
            yield child
        yield from _trees(child)


@pytest.mark.parametrize("surface", ["servers", "summary"])
@pytest.mark.parametrize("dark", [False, True])
def test_highlight_colors_survive_theme_and_selection(tk_root, monkeypatch, surface, dark):
    theme = SMBSeekTheme(use_dark_mode=dark)
    theme.apply_theme_to_application(tk_root)
    settings = SherlockSettings(user_colors={"user1": "#123456", "user2": "#00ff00"})
    # Repeated rows must reuse their tag's contrast calculation. Include a User
    # color darker than its severity, a bright User color, and an empty fallback.
    risks = [
        {"severity": "low", "count": 2, "display_color_tag": tag}
        for tag in ("none", "user1", "user2", "user3")
    ] * 10 + [None, {"severity": "low", "count": 2, "stale": True}]
    rows = [
        {"row_key": f"S:{i + 1}", "status": "success", "sherlock_risk": risk}
        for i, risk in enumerate(risks)
    ]
    module = table if surface == "servers" else summary
    resolver = Mock(wraps=sherlock_foreground)
    monkeypatch.setattr(module, "sherlock_foreground", resolver)
    monkeypatch.setattr(table, "_load_sherlock_settings", lambda _sm: settings)

    def render():
        if surface == "servers":
            table.update_table_display(tree, rows)
            return tk_root, tree
        dialog = summary.show_batch_summary_dialog(
            parent=tk_root, theme=theme, job_type="probe", results=rows,
            show_risk=True, sherlock_settings=settings, show_export=False,
        )
        return dialog, next(_trees(dialog))

    if surface == "servers":
        _frame, tree, _sv, _sh = table.create_server_table(tk_root, theme, {})
    container, tree = render()
    assert resolver.call_count == 4
    expected = {
        "sherlock_low_none": ("#ffff80", "#111111"),
        "sherlock_low_user1": ("#123456", "#ffffff"),
        "sherlock_low_user2": ("#00ff00", "#111111"),
        "sherlock_low_user3": ("#ffff80", "#111111"),
    }
    for _ in range(2):
        for tag, (background, foreground) in expected.items():
            assert str(tree.tag_configure(tag, "background")) == background
            assert str(tree.tag_configure(tag, "foreground")) == foreground
        children = tree.get_children()
        risk_column = "Risk" if surface == "servers" else "risk"
        assert tree.set(children[0], risk_column) == "LOW 2"
        for item in children[-2:]:
            assert not tree.item(item, "tags")
            assert tree.set(item, risk_column) == ""
        tree.selection_set(children[0])
        style = ttk.Style(tree)
        assert style.lookup("Treeview", "foreground", ("selected",)) == "#ffffff"
        assert style.lookup("Treeview", "background", ("selected",)) == theme.colors["accent"]
        tree.selection_remove(children[0])
        assert str(tree.tag_configure("sherlock_low_none", "foreground")) == "#111111"
        theme.toggle_mode(root=tk_root)
    assert resolver.call_count == 4  # theme changes need no recomputation

    # A changed highlight must replace the old foreground as well as background.
    settings.user_colors["user1"] = "#ffff80"
    if surface == "summary":
        container.destroy()
    container, tree = render()
    assert resolver.call_count == 8
    assert str(tree.tag_configure("sherlock_low_user1", "background")) == "#ffff80"
    assert str(tree.tag_configure("sherlock_low_user1", "foreground")) == "#111111"
