"""
Pure (Tk-free) input validators for UnifiedScanDialog.

These functions have no Tkinter dependencies and can be tested without a display.
"""

from __future__ import annotations

_MAX_COUNTRIES = 100


def validate_integer_char(proposed: str) -> bool:
    """Return True if *proposed* is empty or consists solely of digits.

    Designed for use as a Tkinter validatecommand callback.
    """
    return proposed == "" or proposed.isdigit()


def parse_positive_int(
    value_str: str,
    field_name: str,
    *,
    minimum: int = 1,
    maximum: int,
) -> int:
    """Parse *value_str* as a positive integer within [minimum, maximum].

    Raises ValueError with a human-readable message on any failure.
    """
    if not value_str.strip():
        raise ValueError(f"{field_name} is required.")
    try:
        v = int(value_str)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a whole number.") from exc
    if v < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    if v > maximum:
        raise ValueError(f"{field_name} must be {maximum} or less.")
    return v


def parse_and_validate_countries(country_input: str) -> tuple[list[str], str]:
    """Split and validate a comma-separated string of country codes.

    Returns ``(codes, "")`` on success, ``([], error_message)`` on failure.
    Codes are normalised to upper-case.
    """
    if not country_input.strip():
        return [], ""

    codes = [c.strip().upper() for c in country_input.split(",")]
    valid: list[str] = []
    for code in codes:
        if not code:
            continue
        if len(code) < 2 or len(code) > 3:
            return [], f"Invalid country code '{code}': must be 2-3 characters (e.g., US, GB, CA)"
        if not code.isalpha():
            return [], f"Invalid country code '{code}': must contain only letters (e.g., US, GB, CA)"
        valid.append(code)

    if not valid:
        return [], "Please enter at least one valid country code"
    return valid, ""
