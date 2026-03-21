"""Theme runtime toggle coverage for light/dark mode behavior."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.dashboard import DashboardWidget
from gui.utils.style import SMBSeekTheme


@pytest.fixture(scope="module")
def tk_root():
    """Create a hidden Tk root for theme repaint tests."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def _norm(value: str) -> str:
    return str(value).strip().lower()


def test_theme_set_mode_normalization():
    theme = SMBSeekTheme(use_dark_mode=True)

    assert theme.get_mode() == "dark"
    assert theme.set_mode("LIGHT") == "light"
    assert theme.get_mode() == "light"

    # Unknown values safely fall back to light mode.
    assert theme.set_mode("not-a-mode") == "light"
    assert theme.get_mode() == "light"


def test_toggle_mode_rethemes_open_toplevels(tk_root):
    import tkinter as tk

    theme = SMBSeekTheme(use_dark_mode=False)

    frame = tk.Frame(tk_root, bg=theme.colors["card_bg"])
    frame.pack()
    label = tk.Label(frame, text="Status", bg=theme.colors["card_bg"], fg=theme.colors["text"])
    label.pack()
    entry = tk.Entry(
        frame,
        bg=theme.colors["primary_bg"],
        fg=theme.colors["text"],
        insertbackground=theme.colors["text"],
    )
    entry.pack()

    child = tk.Toplevel(tk_root)
    child.withdraw()
    child.configure(background=theme.colors["primary_bg"])
    child_label = tk.Label(child, text="Child", bg=theme.colors["primary_bg"], fg=theme.colors["text"])
    child_label.pack()

    new_mode = theme.toggle_mode(root=tk_root)

    assert new_mode == "dark"
    assert _norm(frame.cget("bg")) == _norm(theme.colors["card_bg"])
    assert _norm(label.cget("bg")) == _norm(theme.colors["card_bg"])
    assert _norm(label.cget("fg")) == _norm(theme.colors["text"])
    assert _norm(entry.cget("bg")) == _norm(theme.colors["primary_bg"])
    assert _norm(child.cget("background")) == _norm(theme.colors["primary_bg"])
    assert _norm(child_label.cget("fg")) == _norm(theme.colors["text"])

    child.destroy()


def test_dashboard_theme_toggle_button_text_reflects_mode():
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.theme = MagicMock()

    dash.theme.get_mode.return_value = "light"
    assert dash._theme_toggle_button_text() == "🌙"

    dash.theme.get_mode.return_value = "dark"
    assert dash._theme_toggle_button_text() == "☀️"


def test_dashboard_toggle_theme_persists_setting_and_restyles():
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.parent = MagicMock()
    dash.settings_manager = MagicMock()
    dash.theme = MagicMock()
    dash.theme.toggle_mode.return_value = "dark"
    dash.theme.get_mode.return_value = "dark"
    dash.theme.colors = {
        "log_bg": "#111418",
        "log_fg": "#f5f5f5",
        "log_placeholder": "#9ea4b3",
    }

    dash.theme_toggle_button = MagicMock()
    dash.theme_toggle_button.winfo_exists.return_value = True
    dash.log_jump_button = None
    dash.log_text_widget = None
    dash.scan_button_state = "idle"
    dash._update_scan_button_state = MagicMock()

    dash._toggle_theme()

    dash.theme.toggle_mode.assert_called_once_with(root=dash.parent)
    dash.settings_manager.set_setting.assert_called_once_with("interface.theme", "dark")
    dash.theme_toggle_button.configure.assert_called_once_with(text="☀️")
    dash.theme.apply_to_widget.assert_called_with(dash.theme_toggle_button, "button_secondary")
    dash._update_scan_button_state.assert_called_once_with("idle")
