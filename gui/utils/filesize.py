"""
Shared file-size formatting helper for GUI components.

Centralised in C1 (modularization refactor, 2026-04-15).
Canonical source was gui.components.unified_browser_window._format_file_size
(identical copy existed in gui.components.file_viewer_window._format_file_size).
"""


def _format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format (e.g., '1.6 MB')."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[unit_index]}"
