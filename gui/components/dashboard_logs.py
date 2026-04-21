"""
Log viewer helpers for DashboardWidget.

Each function takes the dashboard instance (self) and mirrors the original
method behavior from dashboard.py. No UI text or layout changes.
"""

import tkinter as tk
from tkinter import ttk
import queue
from typing import List, Optional


def configure_log_tags(dashboard) -> None:
    """Configure text tags used for ANSI-colored output."""
    if not dashboard.log_text_widget:
        return

    mono_font = dashboard.theme.fonts["mono"]
    bold_font = (mono_font[0], mono_font[1], "bold")
    dashboard.log_text_widget.tag_configure("ansi_bold", font=bold_font)

    # Use theme colors with fallback to original values for visual parity
    colors = dashboard.theme.colors
    color_map = {
        "ansi_fg_black": colors.get("ansi_black", "#7f8796"),
        "ansi_fg_red": colors.get("ansi_red", "#ff7676"),
        "ansi_fg_green": colors.get("ansi_green", "#7dd87d"),
        "ansi_fg_yellow": colors.get("ansi_yellow", "#ffd666"),
        "ansi_fg_blue": colors.get("ansi_blue", "#76b9ff"),
        "ansi_fg_magenta": colors.get("ansi_magenta", "#d692ff"),
        "ansi_fg_cyan": colors.get("ansi_cyan", "#4dd0e1"),
        "ansi_fg_white": colors.get("ansi_white", dashboard.log_fg_color),
        "ansi_fg_bright_black": colors.get("ansi_bright_black", "#a0a7b4"),
        "ansi_fg_bright_red": colors.get("ansi_bright_red", "#ff8b8b"),
        "ansi_fg_bright_green": colors.get("ansi_bright_green", "#8ef79a"),
        "ansi_fg_bright_yellow": colors.get("ansi_bright_yellow", "#ffe082"),
        "ansi_fg_bright_blue": colors.get("ansi_bright_blue", "#90c8ff"),
        "ansi_fg_bright_magenta": colors.get("ansi_bright_magenta", "#f78bff"),
        "ansi_fg_bright_cyan": colors.get("ansi_bright_cyan", "#6fe8ff"),
        "ansi_fg_bright_white": colors.get("ansi_bright_white", "#ffffff")
    }

    for tag, color in color_map.items():
        dashboard.log_text_widget.tag_configure(tag, foreground=color)

    dashboard.log_text_widget.tag_configure(
        "log_placeholder",
        foreground=dashboard.log_placeholder_color
    )


def render_log_placeholder(dashboard) -> None:
    """Display placeholder text when no log output is available."""
    if not dashboard.log_text_widget:
        return

    dashboard.log_text_widget.configure(state=tk.NORMAL)
    dashboard.log_text_widget.delete("1.0", tk.END)
    dashboard.log_text_widget.insert(
        tk.END,
        f"{dashboard.log_placeholder_text}\n",
        ("log_placeholder",)
    )
    dashboard.log_text_widget.configure(state=tk.DISABLED)
    dashboard._log_placeholder_visible = True
    dashboard.log_autoscroll = True
    dashboard._hide_log_jump_button()


def reset_log_output(dashboard, country: Optional[str]) -> None:
    """Clear log output and add a friendly header for the new scan."""
    dashboard._clear_log_output()
    target = country or "global"
    dashboard._append_log_line(f"GUI: awaiting backend output for {target} scan...")


def append_log_line(dashboard, line: str) -> None:
    """Append a raw CLI line to the text widget preserving ANSI colors."""
    if not dashboard.log_text_widget or line is None:
        return

    previous_len = len(dashboard.log_history)
    dashboard.log_history.append(line)

    dashboard.log_text_widget.configure(state=tk.NORMAL)
    if dashboard._log_placeholder_visible:
        dashboard.log_text_widget.delete("1.0", tk.END)
        dashboard._log_placeholder_visible = False

    segments = dashboard._parse_ansi_segments(line)
    if not segments:
        segments = [(line, ())]

    for segment_text, tags in segments:
        if segment_text:
            dashboard.log_text_widget.insert(tk.END, segment_text, tags)
    dashboard.log_text_widget.insert(tk.END, "\n")

    if previous_len == dashboard.log_history.maxlen:
        dashboard.log_text_widget.delete("1.0", "2.0")

    dashboard.log_text_widget.configure(state=tk.DISABLED)

    if dashboard.log_autoscroll:
        dashboard.log_text_widget.see(tk.END)

    dashboard._update_log_autoscroll_state()


def parse_ansi_segments(dashboard, text: str) -> List[tuple]:
    """Split text into (segment, tags) respecting ANSI escape codes."""
    segments = []
    last_end = 0
    active_tags: List[str] = []

    for match in dashboard._ansi_pattern.finditer(text):
        start, end = match.span()
        if start > last_end:
            segments.append((text[last_end:start], tuple(active_tags)))

        codes = match.group(1).split(";") if match.group(1) else ["0"]
        active_tags = dashboard._apply_ansi_codes(active_tags, codes)
        last_end = end

    if last_end < len(text):
        segments.append((text[last_end:], tuple(active_tags)))

    return segments


def apply_ansi_codes(dashboard, active_tags: List[str], codes: List[str]) -> List[str]:
    """Update active tag list based on ANSI code sequence."""
    tags = list(active_tags)
    for code in codes:
        if not code:
            code = "0"

        if code == "0":
            tags.clear()
        elif code == "1":
            if "ansi_bold" not in tags:
                tags.append("ansi_bold")
        elif code in dashboard._ansi_color_tag_map:
            tags = [t for t in tags if t not in dashboard._ansi_color_tags]
            tags.append(dashboard._ansi_color_tag_map[code])

    return tags


def handle_scan_log_line(dashboard, line: str) -> None:
    """Queue log lines coming from background scan threads."""
    if line is None:
        return
    dashboard.log_queue.put(line)


def process_log_queue(dashboard) -> None:
    """Drain queued log lines on the Tk thread."""
    if not dashboard.parent or not dashboard.parent.winfo_exists():
        return

    try:
        while True:
            line = dashboard.log_queue.get_nowait()
            dashboard._append_log_line(line)
    except queue.Empty:
        pass

    dashboard.log_processing_job = dashboard.parent.after(150, dashboard._process_log_queue)


def update_log_autoscroll_state(dashboard, *_args) -> None:
    """Detect whether the viewer is scrolled to the bottom."""
    if not dashboard.log_text_widget:
        return

    at_bottom = dashboard._is_log_at_bottom()
    dashboard.log_autoscroll = at_bottom

    if at_bottom:
        dashboard._hide_log_jump_button()
    else:
        dashboard._show_log_jump_button()


def is_log_at_bottom(dashboard) -> bool:
    """Return True if the viewer is scrolled to the bottom."""
    if not dashboard.log_text_widget:
        return True
    start, end = dashboard.log_text_widget.yview()
    return end >= 0.995


def scroll_log_to_latest(dashboard) -> None:
    """Scroll the viewer to the most recent line and resume autoscroll."""
    if not dashboard.log_text_widget:
        return
    dashboard.log_text_widget.see(tk.END)
    dashboard.log_autoscroll = True
    dashboard._hide_log_jump_button()


def show_log_jump_button(dashboard) -> None:
    """Display the jump-to-latest helper."""
    if dashboard.log_jump_button and not dashboard.log_jump_button.winfo_ismapped():
        dashboard.log_jump_button.pack(side=tk.RIGHT, padx=(5, 0))


def hide_log_jump_button(dashboard) -> None:
    """Hide the jump-to-latest helper."""
    if dashboard.log_jump_button and dashboard.log_jump_button.winfo_ismapped():
        dashboard.log_jump_button.pack_forget()


def copy_log_output(dashboard) -> None:
    """Copy current log contents to clipboard."""
    if not dashboard.log_history:
        return
    try:
        dashboard.parent.clipboard_clear()
        dashboard.parent.clipboard_append("\n".join(dashboard.log_history))
    except tk.TclError:
        pass


def clear_log_output(dashboard) -> None:
    """Clear log viewer and reset placeholder."""
    dashboard.log_history.clear()
    dashboard._render_log_placeholder()


def build_log_viewer(dashboard) -> None:
    """Create expanded live output viewer."""
    log_container = tk.Frame(
        dashboard.progress_frame,
        bg=dashboard.theme.colors["card_bg"],
        highlightthickness=0
    )
    log_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 14))

    header_frame = tk.Frame(log_container, bg=dashboard.theme.colors["card_bg"])
    header_frame.pack(fill=tk.X, pady=(0, 6))

    header_label = tk.Label(
        header_frame,
        text="Live Scan Output",
        bg=dashboard.theme.colors["card_bg"],
        fg=dashboard.theme.colors["text"],
        font=dashboard.theme.fonts["heading"]
    )
    header_label.pack(side=tk.LEFT)

    dashboard.log_jump_button = tk.Button(
        header_frame,
        text="Jump to Latest",
        command=dashboard._scroll_log_to_latest
    )
    dashboard.theme.apply_to_widget(dashboard.log_jump_button, "button_secondary")
    dashboard.log_jump_button.pack(side=tk.RIGHT, padx=(5, 0))
    dashboard.log_jump_button.pack_forget()  # hidden until user scrolls away

    text_frame = tk.Frame(log_container, bg=dashboard.log_bg_color)
    text_frame.pack(fill=tk.BOTH, expand=True)

    scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    base_log_lines = 10
    extra_log_height_px = 300  # 150px original bump + 150px new request
    line_converter = getattr(dashboard, "_pixels_to_text_lines", None)
    extra_lines = line_converter(extra_log_height_px) if callable(line_converter) else max(1, extra_log_height_px // 14)
    expanded_lines = base_log_lines + int(extra_lines)
    dashboard.log_text_widget = tk.Text(
        text_frame,
        height=expanded_lines,
        wrap=tk.NONE,
        bg=dashboard.log_bg_color,
        fg=dashboard.log_fg_color,
        font=dashboard.theme.fonts["mono"],
        state=tk.DISABLED,
        relief="solid",
        borderwidth=1,
        highlightthickness=0,
        insertbackground=dashboard.log_fg_color
    )
    dashboard.log_text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    dashboard.log_text_widget.configure(yscrollcommand=scrollbar.set)
    scrollbar.configure(command=dashboard.log_text_widget.yview)

    # Track manual scrolling to toggle autoscroll state
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<ButtonRelease-1>", "<Shift-MouseWheel>"):
        dashboard.log_text_widget.bind(sequence, dashboard._update_log_autoscroll_state, add="+")

    dashboard._configure_log_tags()
    dashboard._render_log_placeholder()
