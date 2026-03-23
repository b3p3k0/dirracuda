"""
Extension Editor Dialog

Modal dialog for editing extension filters with a two-pane list-based UI.
Provides controls for adding, editing, removing, and moving extensions between
included/excluded lists, with persistence to config.json.

Extracted from batch_extract_dialog.py (Slice 4B refactor).
Pure logic delegated to gui/utils/batch_extract_helpers.py.
"""

import json
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from pathlib import Path
from typing import Dict, List, Optional

from gui.utils.batch_extract_helpers import (
    NO_EXTENSION_TOKEN,
    ensure_no_extension_token,
    load_extension_filters,
    sort_extensions,
    validate_extension,
)
from gui.utils.dialog_helpers import ensure_dialog_focus


class ExtensionEditorDialog:
    """
    Modal dialog for editing extension filters with list-based UI.

    Provides two-pane interface for managing included and excluded extensions
    with validation, sorting, and persistence to config.json.
    """

    def __init__(
        self,
        parent: tk.Toplevel,
        theme,
        config_path: Path,
        initial_included: List[str],
        initial_excluded: List[str]
    ):
        """
        Initialize extension editor dialog.

        Args:
            parent: Parent window
            theme: Theme object for styling
            config_path: Path to config.json
            initial_included: Initial included extensions list
            initial_excluded: Initial excluded extensions list
        """
        self.parent = parent
        self.theme = theme
        self.config_path = config_path
        self.included_extensions = list(initial_included)
        self.excluded_extensions = list(initial_excluded)
        self.window: Optional[tk.Toplevel] = None
        self.result: Optional[tuple] = None

        # Ensure the no-extension token is present (default in included list)
        ensure_no_extension_token(self.included_extensions, self.excluded_extensions)

        # UI widgets (initialized in show())
        self.included_listbox: Optional[tk.Listbox] = None
        self.excluded_listbox: Optional[tk.Listbox] = None
        self.extension_count_label: Optional[tk.Label] = None

    def show(self) -> Optional[tuple]:
        """
        Display dialog and return result.

        Returns:
            Tuple of (included_list, excluded_list) or None if cancelled
        """
        self.window = tk.Toplevel(self.parent)
        self.window.title("Extension Filter Editor")
        self.window.geometry("815x690")
        self.window.transient(self.parent)
        self.window.grab_set()

        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Main container
        main_frame = tk.Frame(self.window)
        if self.theme:
            self.theme.apply_to_widget(main_frame, "main_window")
        main_frame.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        # Create two-column layout
        self._create_list_columns(main_frame)

        # Create control buttons
        self._create_control_buttons(main_frame)

        # Create bottom buttons
        self._create_bottom_buttons(main_frame)

        # Handle window close
        self.window.protocol("WM_DELETE_WINDOW", self._on_cancel)

        if self.theme:
            self.theme.apply_theme_to_application(self.window)

        # Ensure dialog appears on top and gains focus
        ensure_dialog_focus(self.window, self.parent)

        self.parent.wait_window(self.window)
        return self.result

    def _create_list_columns(self, parent: tk.Frame):
        """Create two-column layout with listboxes."""
        # Container for both columns
        columns_frame = tk.Frame(parent)
        columns_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Left column - Included Extensions
        left_frame = tk.Frame(columns_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        included_label = tk.Label(left_frame, text="Included Extensions", font=("TkDefaultFont", 10, "bold"))
        included_label.pack(anchor="w", pady=(0, 5))

        # Listbox with scrollbar
        included_scroll_frame = tk.Frame(left_frame)
        included_scroll_frame.pack(fill=tk.BOTH, expand=True)

        included_scrollbar = tk.Scrollbar(included_scroll_frame)
        included_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.included_listbox = tk.Listbox(
            included_scroll_frame,
            height=20,
            yscrollcommand=included_scrollbar.set,
            selectmode=tk.SINGLE
        )
        self.included_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        included_scrollbar.config(command=self.included_listbox.yview)

        if self.theme:
            self.theme.apply_to_widget(self.included_listbox, "listbox")

        # Right column - Excluded Extensions
        right_frame = tk.Frame(columns_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        excluded_label = tk.Label(right_frame, text="Excluded Extensions", font=("TkDefaultFont", 10, "bold"))
        excluded_label.pack(anchor="w", pady=(0, 5))

        # Listbox with scrollbar
        excluded_scroll_frame = tk.Frame(right_frame)
        excluded_scroll_frame.pack(fill=tk.BOTH, expand=True)

        excluded_scrollbar = tk.Scrollbar(excluded_scroll_frame)
        excluded_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.excluded_listbox = tk.Listbox(
            excluded_scroll_frame,
            height=20,
            yscrollcommand=excluded_scrollbar.set,
            selectmode=tk.SINGLE
        )
        self.excluded_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        excluded_scrollbar.config(command=self.excluded_listbox.yview)

        if self.theme:
            self.theme.apply_to_widget(self.excluded_listbox, "listbox")

        # Populate and sort lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

    def _create_control_buttons(self, parent: tk.Frame):
        """Create control buttons for list operations."""
        control_frame = tk.Frame(parent)
        control_frame.pack(fill=tk.X, pady=10)

        # Left side buttons (Add, Edit, Remove)
        left_buttons = tk.Frame(control_frame)
        left_buttons.pack(side=tk.LEFT)

        add_btn = tk.Button(left_buttons, text="Add", command=self._on_add, width=10)
        edit_btn = tk.Button(left_buttons, text="Edit", command=self._on_edit, width=10)
        remove_btn = tk.Button(left_buttons, text="Remove", command=self._on_remove, width=10)

        for btn in (add_btn, edit_btn, remove_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=5)

        # Right side buttons (Move operations)
        right_buttons = tk.Frame(control_frame)
        right_buttons.pack(side=tk.RIGHT)

        move_to_excluded_btn = tk.Button(
            right_buttons,
            text="→ Move to Excluded",
            command=self._on_move_to_excluded,
            width=18
        )
        move_to_included_btn = tk.Button(
            right_buttons,
            text="← Move to Included",
            command=self._on_move_to_included,
            width=18
        )

        for btn in (move_to_excluded_btn, move_to_included_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_secondary")
            btn.pack(side=tk.LEFT, padx=5)

    def _create_bottom_buttons(self, parent: tk.Frame):
        """Create bottom action buttons."""
        button_frame = tk.Frame(parent)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        # Left side - Reset button
        reset_btn = tk.Button(
            button_frame,
            text="Reset to Defaults",
            command=self._on_reset,
            width=15
        )
        if self.theme:
            self.theme.apply_to_widget(reset_btn, "button_secondary")
        reset_btn.pack(side=tk.LEFT)

        # Right side - Save and Cancel buttons
        cancel_btn = tk.Button(button_frame, text="Cancel", command=self._on_cancel, width=10)
        save_btn = tk.Button(button_frame, text="Save", command=self._on_save, width=10)

        for btn in (cancel_btn, save_btn):
            if self.theme:
                self.theme.apply_to_widget(btn, "button_primary")

        save_btn.pack(side=tk.RIGHT, padx=(5, 0))
        cancel_btn.pack(side=tk.RIGHT)

    def _sort_list(self, listbox: tk.Listbox, extensions: List[str]) -> List[str]:
        """Sort extensions and sync listbox.

        Delegates pure sort/dedup logic to sort_extensions(); handles
        listbox widget update here.

        Args:
            listbox: Tkinter Listbox widget to update.
            extensions: List of extension strings (mutated in-place).

        Returns:
            Sorted list.
        """
        sorted_exts = sort_extensions(extensions)  # mutates extensions in-place
        listbox.delete(0, tk.END)
        for ext in sorted_exts:
            listbox.insert(tk.END, ext)
        return sorted_exts

    def _get_active_list(self) -> tuple:
        """
        Determine which listbox has focus and return (active_listbox, active_list, other_list).

        Returns:
            Tuple of (active_listbox, active_extensions, other_extensions)
        """
        # Check which widget has focus
        focused = self.window.focus_get()

        if focused == self.included_listbox:
            return (self.included_listbox, self.included_extensions, self.excluded_extensions)
        elif focused == self.excluded_listbox:
            return (self.excluded_listbox, self.excluded_extensions, self.included_extensions)
        else:
            # Default to included if no focus
            return (self.included_listbox, self.included_extensions, self.excluded_extensions)

    def _on_add(self):
        """Prompt for new extension and add to active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Prompt user
        ext = simpledialog.askstring(
            "Add Extension",
            "Enter file extension (e.g., .txt or txt):",
            parent=self.window
        )

        if ext is None:  # User cancelled
            return

        # Validate
        is_valid, normalized_ext, error = validate_extension(ext, active_list, other_list)

        if not is_valid:
            messagebox.showerror("Invalid Extension", error, parent=self.window)
            return

        # Add and sort
        active_list.append(normalized_ext)
        self._sort_list(active_listbox, active_list)

        # Select the newly added item
        idx = active_list.index(normalized_ext)
        active_listbox.selection_clear(0, tk.END)
        active_listbox.selection_set(idx)
        active_listbox.see(idx)

    def _on_edit(self):
        """Edit selected extension in active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Get selection
        selection = active_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to edit", parent=self.window)
            return

        idx = selection[0]
        current_ext = active_list[idx]

        # Prompt user
        new_ext = simpledialog.askstring(
            "Edit Extension",
            "Edit file extension:",
            initialvalue=current_ext,
            parent=self.window
        )

        if new_ext is None:  # User cancelled
            return

        # Create list without current item for validation
        temp_list = active_list[:idx] + active_list[idx+1:]

        # Validate
        is_valid, normalized_ext, error = validate_extension(new_ext, temp_list, other_list)

        if not is_valid:
            messagebox.showerror("Invalid Extension", error, parent=self.window)
            return

        # Update and sort
        active_list[idx] = normalized_ext
        self._sort_list(active_listbox, active_list)

        # Re-select the item at its new position
        new_idx = active_list.index(normalized_ext)
        active_listbox.selection_clear(0, tk.END)
        active_listbox.selection_set(new_idx)
        active_listbox.see(new_idx)

    def _on_remove(self):
        """Remove selected extension from active list."""
        active_listbox, active_list, other_list = self._get_active_list()

        # Get selection
        selection = active_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to remove", parent=self.window)
            return

        idx = selection[0]
        ext = active_list[idx]

        # Confirm removal
        if not messagebox.askyesno(
            "Confirm Removal",
            f"Remove extension '{ext}'?",
            parent=self.window
        ):
            return

        # Remove
        active_list.pop(idx)
        active_listbox.delete(idx)

        # Select next item if available
        if active_listbox.size() > 0:
            new_idx = min(idx, active_listbox.size() - 1)
            active_listbox.selection_set(new_idx)
            active_listbox.see(new_idx)

    def _on_move_to_excluded(self):
        """Move selected extension from included to excluded."""
        selection = self.included_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to move", parent=self.window)
            return

        idx = selection[0]
        ext = self.included_extensions[idx]

        # Move
        self.included_extensions.pop(idx)
        self.excluded_extensions.append(ext)

        # Sort both lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

        # Select the moved item in excluded list
        new_idx = self.excluded_extensions.index(ext)
        self.excluded_listbox.selection_clear(0, tk.END)
        self.excluded_listbox.selection_set(new_idx)
        self.excluded_listbox.see(new_idx)
        self.excluded_listbox.focus_set()

    def _on_move_to_included(self):
        """Move selected extension from excluded to included."""
        selection = self.excluded_listbox.curselection()
        if not selection:
            messagebox.showinfo("No Selection", "Please select an extension to move", parent=self.window)
            return

        idx = selection[0]
        ext = self.excluded_extensions[idx]

        # Move
        self.excluded_extensions.pop(idx)
        self.included_extensions.append(ext)

        # Sort both lists
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

        # Select the moved item in included list
        new_idx = self.included_extensions.index(ext)
        self.included_listbox.selection_clear(0, tk.END)
        self.included_listbox.selection_set(new_idx)
        self.included_listbox.see(new_idx)
        self.included_listbox.focus_set()

    def _on_reset(self):
        """Reset to default extensions from config."""
        if not messagebox.askyesno(
            "Reset to Defaults",
            "This will reset all extensions to the defaults from config.json. Continue?",
            parent=self.window
        ):
            return

        # Reload from config (raw, no normalization — token injected explicitly)
        filters = load_extension_filters(self.config_path, normalize=False)
        self.included_extensions = list(filters["included_extensions"])
        self.excluded_extensions = list(filters["excluded_extensions"])
        ensure_no_extension_token(self.included_extensions, self.excluded_extensions)

        # Update both listboxes
        self._sort_list(self.included_listbox, self.included_extensions)
        self._sort_list(self.excluded_listbox, self.excluded_extensions)

    def _on_save(self):
        """Save changes to config.json and close."""
        if not self.config_path or not self.config_path.exists():
            messagebox.showerror(
                "Configuration Error",
                "Cannot find config.json. Please check your configuration path.",
                parent=self.window
            )
            return

        try:
            # Load current config
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            config_data = json.loads(self.config_path.read_text(encoding="utf-8"))

            # Update file_collection section
            if "file_collection" not in config_data:
                config_data["file_collection"] = {}

            config_data["file_collection"]["included_extensions"] = self.included_extensions
            config_data["file_collection"]["excluded_extensions"] = self.excluded_extensions

            # Write back to file
            self.config_path.write_text(
                json.dumps(config_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

            # Set result and close
            self.result = (self.included_extensions, self.excluded_extensions)
            self.window.destroy()

        except Exception as e:
            messagebox.showerror(
                "Save Failed",
                f"Failed to save configuration: {str(e)}",
                parent=self.window
            )

    def _on_cancel(self):
        """Close without saving."""
        self.result = None
        self.window.destroy()
