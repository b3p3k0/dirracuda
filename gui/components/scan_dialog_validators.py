"""
Pure (Tk-free) numeric validators for ScanDialog.

Extracted from scan_dialog.py (Slice 7B refactor).
These functions have no Tkinter dependencies and can be tested without a display.

Note: ``validate_integer_char`` and ``parse_and_validate_countries`` live in
``unified_scan_validators`` and are imported directly by scan_dialog.py.
This module exists solely for ``parse_positive_int``, whose signature differs
from the unified version (``minimum`` defaults to 0; ``maximum`` is optional).
"""

from __future__ import annotations

from typing import Optional


def parse_positive_int(
    value_str: str,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: Optional[int] = None,
) -> int:
    """Parse *value_str* as an integer within [minimum, maximum].

    Raises ``ValueError`` with a human-readable message on any failure.
    ``maximum=None`` means no upper bound.
    """
    if value_str == "":
        raise ValueError(f"{field_name} is required.")

    try:
        value = int(value_str)
    except ValueError:
        raise ValueError(f"{field_name} must be a whole number.")

    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")

    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be {maximum} or less.")

    return value
