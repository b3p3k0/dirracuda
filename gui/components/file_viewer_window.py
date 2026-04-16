"""
Read-only file viewer for Dirracuda with text and hex display modes.

Capabilities:
- View file contents in text mode with encoding selection
- View file contents in hex mode with offset/hex/ASCII columns
- Auto-detect binary files and switch to hex mode
- Optional save to quarantine
"""

import tkinter as tk
from tkinter import ttk
from gui.utils import safe_messagebox as messagebox
from typing import Optional, Callable
from pathlib import Path

try:
    from gui.utils.style import get_theme
except ImportError:
    from utils.style import get_theme

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:
    from utils.dialog_helpers import ensure_dialog_focus

try:
    from gui.utils.filesize import _format_file_size
except ImportError:
    from utils.filesize import _format_file_size  # type: ignore[no-redef]


ENCODINGS = ["utf-8", "ascii", "latin-1", "utf-16", "windows-1252"]
HEX_BYTES_PER_ROW = 16


def is_binary_content(data: bytes, sample_size: int = 8192) -> bool:
    """
    Check if content appears to be binary.

    Args:
        data: File content bytes
        sample_size: Number of bytes to sample from start

    Returns:
        True if content appears to be binary
    """
    if not data:
        return False
    sample = data[:sample_size]
    # Null bytes are a strong indicator of binary
    if b'\x00' in sample:
        return True
    # Check for high ratio of non-printable ASCII (excluding tab, newline, carriage return)
    non_printable = sum(1 for b in sample if b < 32 and b not in (9, 10, 13))
    return non_printable / max(len(sample), 1) > 0.1


class FileViewerWindow:
    """Read-only file viewer with text/hex modes."""

    def __init__(
        self,
        parent: tk.Widget,
        file_path: str,
        content: bytes,
        file_size: int,
        theme=None,
        start_in_hex: bool = False,
        on_save_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Initialize file viewer window.

        Args:
            parent: Parent widget
            file_path: Display path (for title bar)
            content: File content bytes
            file_size: Original file size (for truncation detection)
            theme: Theme object (optional, will use default if None)
            start_in_hex: Whether to start in hex mode
            on_save_callback: Optional callback when user clicks Save to Quarantine
        """
        self.parent = parent
        self.file_path = file_path
        self.content = content
        self.file_size = file_size
        self.theme = theme or get_theme()
        self.on_save_callback = on_save_callback
        self.truncated = len(content) < file_size

        # State
        self.current_mode = "hex" if start_in_hex else "text"
        self.current_encoding = "utf-8"

        # UI components
        self.window: Optional[tk.Toplevel] = None
        self.text_widget: Optional[tk.Text] = None
        self.mode_var: Optional[tk.StringVar] = None
        self.encoding_var: Optional[tk.StringVar] = None
        self.encoding_combo: Optional[ttk.Combobox] = None
        self.status_var: Optional[tk.StringVar] = None

        self._build_window()
        self._render_content()

    def _build_window(self) -> None:
        """Create the viewer window."""
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"File Viewer - {self.file_path}")
        self.window.geometry("900x650")
        self.window.minsize(600, 400)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # --- Toolbar frame ---
        toolbar = tk.Frame(self.window)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        if self.theme:
            self.theme.apply_to_widget(toolbar, "main_window")

        # Mode selection
        mode_label = tk.Label(toolbar, text="Mode:")
        mode_label.pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value=self.current_mode)

        text_rb = tk.Radiobutton(
            toolbar, text="Text", variable=self.mode_var, value="text",
            command=self._on_mode_change
        )
        text_rb.pack(side=tk.LEFT, padx=(5, 0))

        hex_rb = tk.Radiobutton(
            toolbar, text="Hex", variable=self.mode_var, value="hex",
            command=self._on_mode_change
        )
        hex_rb.pack(side=tk.LEFT, padx=(5, 15))

        # Encoding selection (only for text mode)
        self.encoding_label = tk.Label(toolbar, text="Encoding:")
        self.encoding_label.pack(side=tk.LEFT)

        self.encoding_var = tk.StringVar(value=self.current_encoding)
        self.encoding_combo = ttk.Combobox(
            toolbar, textvariable=self.encoding_var,
            values=ENCODINGS, state="readonly", width=12
        )
        self.encoding_combo.pack(side=tk.LEFT, padx=(5, 15))
        self.encoding_combo.bind("<<ComboboxSelected>>", self._on_encoding_change)

        # File size display
        size_text = _format_file_size(self.file_size)
        if self.truncated:
            size_text += f" (showing {_format_file_size(len(self.content))})"
        size_label = tk.Label(toolbar, text=f"Size: {size_text}")
        size_label.pack(side=tk.RIGHT)

        if self.theme:
            self.theme.apply_to_widget(mode_label, "label")
            self.theme.apply_to_widget(text_rb, "checkbox")
            self.theme.apply_to_widget(hex_rb, "checkbox")
            self.theme.apply_to_widget(self.encoding_label, "label")
            self.theme.apply_to_widget(size_label, "label")

        # --- Content area ---
        content_frame = tk.Frame(self.window)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        if self.theme:
            self.theme.apply_to_widget(content_frame, "main_window")

        # Use a reliable monospace font - critical for hex view alignment
        mono_font = ("Courier", 11)  # Universal monospace, slightly larger for readability
        try:
            if hasattr(self.theme, 'fonts') and isinstance(self.theme.fonts, dict):
                mono_font = self.theme.fonts.get("mono", mono_font)
        except Exception:
            pass

        self.text_widget = tk.Text(
            content_frame,
            wrap=tk.NONE,
            font=mono_font,
            bg=self.theme.colors.get("primary_bg", "#1e1e1e") if self.theme else "#1e1e1e",
            fg=self.theme.colors.get("text", "#ffffff") if self.theme else "#ffffff",
            insertbackground=self.theme.colors.get("text", "#ffffff") if self.theme else "#ffffff",
            state=tk.DISABLED,
        )

        v_scroll = ttk.Scrollbar(content_frame, orient="vertical", command=self.text_widget.yview)
        h_scroll = ttk.Scrollbar(content_frame, orient="horizontal", command=self.text_widget.xview)
        self.text_widget.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.text_widget.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        content_frame.grid_rowconfigure(0, weight=1)
        content_frame.grid_columnconfigure(0, weight=1)

        # --- Status bar ---
        self.status_var = tk.StringVar()
        status_label = tk.Label(self.window, textvariable=self.status_var, anchor="w")
        status_label.pack(fill=tk.X, padx=10, pady=(0, 5))
        if self.theme:
            self.theme.apply_to_widget(status_label, "status_bar")

        # --- Button bar ---
        button_frame = tk.Frame(self.window)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        if self.theme:
            self.theme.apply_to_widget(button_frame, "main_window")

        if self.on_save_callback:
            save_btn = tk.Button(
                button_frame, text="Save to Quarantine",
                command=self._on_save
            )
            if self.theme:
                self.theme.apply_to_widget(save_btn, "button_secondary")
            save_btn.pack(side=tk.LEFT)

        close_btn = tk.Button(button_frame, text="Close", command=self._on_close)
        if self.theme:
            self.theme.apply_to_widget(close_btn, "button_primary")
        close_btn.pack(side=tk.RIGHT)

        # Update encoding visibility based on mode
        self._update_encoding_visibility()

        # Keyboard shortcuts
        self.window.bind("<Escape>", lambda e: self._on_close())
        self.window.bind("<Control-w>", lambda e: self._on_close())

        if self.theme:
            self.theme.apply_theme_to_application(self.window)

        # Ensure focus
        ensure_dialog_focus(self.window, self.parent)

    def _on_mode_change(self) -> None:
        """Handle mode radio button change."""
        new_mode = self.mode_var.get()
        if new_mode != self.current_mode:
            self.current_mode = new_mode
            self._update_encoding_visibility()
            self._render_content()

    def _on_encoding_change(self, event=None) -> None:
        """Handle encoding combobox change."""
        new_encoding = self.encoding_var.get()
        if new_encoding != self.current_encoding:
            self.current_encoding = new_encoding
            self._render_content()

    def _update_encoding_visibility(self) -> None:
        """Show/hide encoding selector based on mode."""
        if self.current_mode == "text":
            self.encoding_label.pack(side=tk.LEFT)
            self.encoding_combo.pack(side=tk.LEFT, padx=(5, 15))
        else:
            self.encoding_label.pack_forget()
            self.encoding_combo.pack_forget()

    def _render_content(self) -> None:
        """Render content in current mode."""
        if self.current_mode == "text":
            self._render_text()
        else:
            self._render_hex()

    def _render_text(self) -> None:
        """Render content as text with current encoding."""
        self.text_widget.configure(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)

        if not self.content:
            self.text_widget.insert("1.0", "(empty file)")
            self._set_status("Empty file")
        else:
            try:
                text = self.content.decode(self.current_encoding, errors="replace")
                self.text_widget.insert("1.0", text)
                line_count = text.count('\n') + 1
                self._set_status(f"Text mode | {line_count} lines | {self.current_encoding}")
            except Exception as e:
                self.text_widget.insert("1.0", f"(decode error: {e})")
                self._set_status(f"Decode error: {e}")

        self.text_widget.configure(state=tk.DISABLED)

    def _render_hex(self) -> None:
        """Render content as hex dump."""
        self.text_widget.configure(state=tk.NORMAL)
        self.text_widget.delete("1.0", tk.END)

        if not self.content:
            self.text_widget.insert("1.0", "(empty file)")
            self._set_status("Empty file")
        else:
            lines = []
            for offset in range(0, len(self.content), HEX_BYTES_PER_ROW):
                chunk = self.content[offset:offset + HEX_BYTES_PER_ROW]
                lines.append(self._format_hex_line(offset, chunk))

            self.text_widget.insert("1.0", "\n".join(lines))
            self._set_status(f"Hex mode | {len(self.content)} bytes | {len(lines)} rows")

        self.text_widget.configure(state=tk.DISABLED)

    def _format_hex_line(self, offset: int, data: bytes) -> str:
        """
        Format a single line of hex dump.

        Format: 00000000  89 50 4E 47 0D 0A 1A 0A  00 00 00 0D 49 48 44 52  |.PNG........IHDR|
        """
        # Offset column (8 hex digits)
        offset_str = f"{offset:08X}"

        # Hex bytes (two groups of 8 bytes)
        hex_parts = []
        for i in range(HEX_BYTES_PER_ROW):
            if i < len(data):
                hex_parts.append(f"{data[i]:02X}")
            else:
                hex_parts.append("  ")
            if i == 7:
                hex_parts.append("")  # Extra space between groups

        hex_str = " ".join(hex_parts)

        # ASCII column
        ascii_chars = []
        for b in data:
            if 32 <= b < 127:
                ascii_chars.append(chr(b))
            else:
                ascii_chars.append(".")
        ascii_str = "".join(ascii_chars).ljust(HEX_BYTES_PER_ROW)

        return f"{offset_str}  {hex_str}  |{ascii_str}|"

    def _set_status(self, text: str) -> None:
        """Update status bar text."""
        if self.truncated:
            text = f"{text} (truncated)"
        self.status_var.set(text)

    def _on_save(self) -> None:
        """Handle Save to Quarantine button."""
        if self.on_save_callback:
            self.on_save_callback()
            messagebox.showinfo(
                "Saved",
                "File has been saved to quarantine.",
                parent=self.window
            )

    def _on_close(self) -> None:
        """Close the viewer window."""
        if self.window:
            self.window.destroy()
            self.window = None


def open_file_viewer(
    parent: tk.Widget,
    file_path: str,
    content: bytes,
    file_size: int,
    theme=None,
    on_save_callback: Optional[Callable[[], None]] = None,
) -> FileViewerWindow:
    """
    Open a file viewer window.

    Args:
        parent: Parent widget
        file_path: Display path for title bar
        content: File content bytes
        file_size: Original file size
        theme: Theme object (optional)
        on_save_callback: Optional callback for Save to Quarantine

    Returns:
        FileViewerWindow instance
    """
    start_in_hex = is_binary_content(content)
    return FileViewerWindow(
        parent=parent,
        file_path=file_path,
        content=content,
        file_size=file_size,
        theme=theme,
        start_in_hex=start_in_hex,
        on_save_callback=on_save_callback,
    )
