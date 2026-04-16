"""
Shared boolean coercion helper for GUI components.

Centralised in C1 (modularization refactor, 2026-04-15).
Canonical source was gui.components.unified_browser_window._coerce_bool.
"""
from typing import Any


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default
