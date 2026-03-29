"""
Dirracuda Configuration Editor Window

Simple text editor-like interface for Dirracuda configuration files.
Provides basic open, save, and cancel functionality with JSON validation.

Design Decision: Simple text editor approach for direct configuration editing
without the complexity of form-based interfaces.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
from pathlib import Path
from typing import Optional
import sys

from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus


class SimpleConfigEditorWindow:
    """
    Simple configuration editor window with text editor interface.
    
    Provides basic text editing functionality for configuration files with:
    - Text area for direct JSON editing
    - Open, save, and cancel buttons
    - Configuration file path display
    - JSON syntax validation before saving
    """
    
    def __init__(self, parent: tk.Widget, config_path: str = None):
        """
        Initialize configuration editor window.
        
        Args:
            parent: Parent widget
            config_path: Path to configuration file (required)
        """
        if config_path is None:
            raise ValueError("config_path is required - no default path available")
        
        self.parent = parent
        self.config_path = Path(config_path).resolve()
        self.theme = get_theme()
        
        # Configuration data
        self.original_content = ""
        self.has_changes = False
        
        # UI components
        self.window = None
        self.text_editor = None
        self.path_label = None
        self.status_label = None
        
        self._create_window()
        self._load_configuration()
    
    def _create_window(self) -> None:
        """Create the configuration editor window."""
        self.window = tk.Toplevel(self.parent)
        self.window.title("Dirracuda - Configuration Editor")
        self.window.geometry("800x700")
        self.window.minsize(600, 400)
        
        # Apply theme
        self.theme.apply_to_widget(self.window, "main_window")
        
        # Make window modal
        self.window.transient(self.parent)
        self.window.grab_set()
        
        # Center window
        self._center_window()
        
        # Build UI
        self._create_header()
        self._create_text_editor()
        self._create_button_panel()

        # Setup event handlers
        self._setup_event_handlers()

        # Ensure window appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.window, self.parent)

    def _center_window(self) -> None:
        """Center window on parent."""
        self.window.update_idletasks()
        
        # Get parent window position and size
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y() 
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()
        
        # Calculate center position
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = parent_x + (parent_width // 2) - (width // 2)
        y = parent_y + (parent_height // 2) - (height // 2)
        
        self.window.geometry(f"{width}x{height}+{x}+{y}")
    
    def _create_header(self) -> None:
        """Create window header with title and file path."""
        header_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        # Title
        title_label = self.theme.create_styled_label(
            header_frame,
            "Configuration Editor",
            "heading"
        )
        title_label.pack(anchor="w")
        
        # Config file path (small font for info only)
        self.path_label = self.theme.create_styled_label(
            header_frame,
            f"File: {self.config_path}",
            "small",
            fg=self.theme.colors["text_secondary"]
        )
        self.path_label.pack(anchor="w", pady=(2, 0))
        
        # Status indicator
        self.status_label = self.theme.create_styled_label(
            header_frame,
            "No changes",
            "small",
            fg=self.theme.colors["success"]
        )
        self.status_label.pack(anchor="w", pady=(2, 0))
    
    def _create_text_editor(self) -> None:
        """Create text editor with scrollbars."""
        # Editor frame
        editor_frame = tk.Frame(self.window)
        editor_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Text widget with scrollbars
        self.text_editor = tk.Text(
            editor_frame,
            wrap=tk.NONE,
            font=self.theme.fonts["mono"],
            bg=self.theme.colors["primary_bg"],
            fg=self.theme.colors["text"],
            insertbackground=self.theme.colors["text"],
            selectbackground=self.theme.colors["accent"],
            selectforeground="white"
        )
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(editor_frame, orient="vertical", command=self.text_editor.yview)
        h_scrollbar = ttk.Scrollbar(editor_frame, orient="horizontal", command=self.text_editor.xview)
        
        self.text_editor.configure(yscrollcommand=v_scrollbar.set)
        self.text_editor.configure(xscrollcommand=h_scrollbar.set)
        
        # Grid layout
        self.text_editor.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)
        
        # Bind change event
        self.text_editor.bind("<KeyRelease>", self._on_text_change)
        self.text_editor.bind("<Button-1>", self._on_text_change)
        self.text_editor.bind("<ButtonRelease-1>", self._on_text_change)
    
    def _create_button_panel(self) -> None:
        """Create button panel with Open, Save, Cancel."""
        button_frame = tk.Frame(self.window)
        self.theme.apply_to_widget(button_frame, "main_window")
        button_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        
        # Left side - Open button
        open_button = tk.Button(
            button_frame,
            text="📁 Open",
            command=self._open_config
        )
        self.theme.apply_to_widget(open_button, "button_secondary")
        open_button.pack(side=tk.LEFT)
        
        # Right side - Save and Cancel
        cancel_button = tk.Button(
            button_frame,
            text="Cancel",
            command=self._cancel_changes
        )
        self.theme.apply_to_widget(cancel_button, "button_secondary")
        cancel_button.pack(side=tk.RIGHT, padx=(10, 0))
        
        save_button = tk.Button(
            button_frame,
            text="💾 Save",
            command=self._save_configuration
        )
        self.theme.apply_to_widget(save_button, "button_primary")
        save_button.pack(side=tk.RIGHT)
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers."""
        self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)
        
        # Keyboard shortcuts
        self.window.bind("<Control-s>", lambda e: self._save_configuration())
        self.window.bind("<Control-o>", lambda e: self._open_config())
        self.window.bind("<Escape>", lambda e: self._cancel_changes())
    
    def _load_configuration(self) -> None:
        """Load configuration from current file path."""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Try to format JSON for better display
                try:
                    parsed_json = json.loads(content)
                    formatted_content = json.dumps(parsed_json, indent=2)
                except json.JSONDecodeError:
                    # If invalid JSON, show as-is
                    formatted_content = content
                
                self.text_editor.delete(1.0, tk.END)
                self.text_editor.insert(1.0, formatted_content)
                self.original_content = formatted_content
            else:
                # File doesn't exist, show default empty config
                default_content = "{\n  \"shodan\": {\n    \"api_key\": \"\"\n  }\n}"
                self.text_editor.delete(1.0, tk.END)
                self.text_editor.insert(1.0, default_content)
                self.original_content = default_content
            
            self.has_changes = False
            self._update_status_display()
            
        except Exception as e:
            messagebox.showerror(
                "Load Error",
                f"Failed to load configuration file:\n{str(e)}"
            )
    
    def _open_config(self) -> None:
        """Open a different configuration file."""
        if self.has_changes:
            result = messagebox.askyesnocancel(
                "Unsaved Changes",
                "You have unsaved changes. Save before opening another file?"
            )
            if result is True:  # Save first
                if not self._save_configuration():
                    return
            elif result is None:  # Cancel
                return
        
        # Choose new file
        filename = filedialog.askopenfilename(
            title="Open Configuration File",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=str(self.config_path.parent)
        )
        
        if filename:
            self.config_path = Path(filename).resolve()
            self.path_label.configure(text=f"File: {self.config_path}")
            self._load_configuration()
    
    def _save_configuration(self) -> bool:
        """
        Save configuration to file.
        
        Returns:
            True if save was successful, False otherwise
        """
        try:
            # Get current text content
            content = self.text_editor.get(1.0, tk.END).strip()
            
            # Validate JSON syntax
            try:
                json.loads(content)
            except json.JSONDecodeError as e:
                messagebox.showerror(
                    "Invalid JSON",
                    f"Configuration contains invalid JSON syntax:\n\n{str(e)}\n\n"
                    "Please fix the JSON syntax before saving."
                )
                return False
            
            # Create backup if file exists
            if self.config_path.exists():
                backup_path = self.config_path.with_suffix(".bak")
                import shutil
                shutil.copy2(self.config_path, backup_path)
            
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save configuration
            with open(self.config_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Update state
            self.original_content = content
            self.has_changes = False
            self._update_status_display()
            
            messagebox.showinfo(
                "Configuration Saved",
                f"Configuration saved successfully!\n\n"
                f"File: {self.config_path}\n\n"
                "New settings will be used for future backend operations."
            )
            
            # Close window after successful save
            self.window.destroy()
            return True
            
        except Exception as e:
            messagebox.showerror(
                "Save Error",
                f"Failed to save configuration:\n{str(e)}"
            )
            return False
    
    def _cancel_changes(self) -> None:
        """Cancel changes and close window."""
        if self.has_changes:
            result = messagebox.askyesno(
                "Discard Changes",
                "You have unsaved changes. Discard them and close?"
            )
            if not result:
                return
        
        self.window.destroy()
    
    def _on_window_close(self) -> None:
        """Handle window close event."""
        self._cancel_changes()
    
    def _on_text_change(self, event=None) -> None:
        """Handle text change events."""
        current_content = self.text_editor.get(1.0, tk.END).strip()
        self.has_changes = (current_content != self.original_content)
        self._update_status_display()
    
    def _update_status_display(self) -> None:
        """Update status display based on change state."""
        if self.has_changes:
            self.status_label.configure(
                text="Unsaved changes",
                fg=self.theme.colors["warning"]
            )
        else:
            self.status_label.configure(
                text="No changes",
                fg=self.theme.colors["success"]
            )


def open_config_editor_window(parent: tk.Widget, config_path: str) -> None:
    """
    Open simplified configuration editor window.
    
    Args:
        parent: Parent widget
        config_path: Path to configuration file
    """
    SimpleConfigEditorWindow(parent, config_path)
