"""Exercise configuration dialog placement with real Tk windows."""

import tkinter as tk
from unittest.mock import MagicMock

import pytest

from gui.components.app_config_dialog import AppConfigDialog
from gui.utils.settings_manager import SettingsManager


@pytest.mark.gui_smoke
@pytest.mark.parametrize("close_method", ["cancel", "window_close"])
def test_config_dialog_restores_size_and_position_after_close(tmp_path, close_method):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable")

    def open_dialog():
        dialog = AppConfigDialog.__new__(AppConfigDialog)
        dialog.parent = root
        dialog.settings_manager = SettingsManager(str(tmp_path))
        dialog.theme = MagicMock()
        # Keep the real dialog lifecycle, isolating configuration and form data.
        for method in ("_create_button_panel", "_create_header", "_create_sections", "_validate_all_fields"):
            setattr(dialog, method, MagicMock())
        dialog._create_dialog()
        root.update()
        return dialog

    try:
        for cycle in range(3):
            dialog = open_dialog()
            if cycle == 0:
                dialog.dialog.geometry("980x820+110+90")
                root.update()
            assert dialog.dialog.geometry() == "980x820+110+90"
            if close_method == "cancel":
                dialog._on_cancel()
            else:
                # Invoke Tk's actual title-bar close protocol.
                root.tk.eval(dialog.dialog.protocol("WM_DELETE_WINDOW"))
            assert SettingsManager(str(tmp_path)).get_setting(
                "windows.app_config.geometry"
            ) == "980x820+110+90"
    finally:
        root.destroy()
