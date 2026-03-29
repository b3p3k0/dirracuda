"""
Shared utility functions for Dirracuda toolkit.

Contains common utility functions used across multiple modules.
"""

import re

# String filter validation constants
MAX_STRING_FILTER_LENGTH = 100
STRING_FILTER_PATTERN = re.compile(r'^[A-Za-z0-9\s\-\_\.\,\@\:\#\(\)\/\\\'"&\+\!\?\$]+$')


def format_string_for_shodan(value: str) -> str:
    """
    Format a user-supplied string for safe inclusion in Shodan queries.

    Args:
        value: Raw string from CLI/config

    Returns:
        Quoted and escaped string

    Raises:
        ValueError: If the string is empty, too long, or contains invalid characters
    """
    if value is None:
        raise ValueError("String filters cannot be empty")

    trimmed_value = value.strip()
    if not trimmed_value:
        raise ValueError("String filters cannot be empty")

    if len(trimmed_value) > MAX_STRING_FILTER_LENGTH:
        raise ValueError(f"String filters cannot exceed {MAX_STRING_FILTER_LENGTH} characters")

    if not STRING_FILTER_PATTERN.fullmatch(trimmed_value):
        raise ValueError(
            "String filters contain invalid characters. Allowed: letters, numbers, spaces, and - _ . , @ : # / \\ ' \" & + ! ? $ ( )"
        )

    escaped_value = trimmed_value.replace('"', '\\"')
    return f'"{escaped_value}"'
