"""
Sherlock settings and pattern data model.

Pure data + validation only: severity ordering, the pattern dataclass, default
colors and color validation, the settings container, and the built-in pattern
catalog. No GUI, no DB, no persistence (config-store registration is C3).
"""

from __future__ import annotations

import dataclasses
import enum
import re
from typing import Dict, List, Optional


class Severity(enum.IntEnum):
    """Finding severity. Integer values give precedence (HIGH > MED > LOW)."""

    LOW = 1
    MED = 2
    HIGH = 3

    @property
    def display_name(self) -> str:
        return self.name

    def display_text(self, count: int) -> str:
        return "{0} {1}".format(self.name, count)


# Default severity colors (see SPEC Severity Defaults table).
DEFAULT_COLORS: Dict[Severity, str] = {
    Severity.HIGH: "#ff4d4d",
    Severity.MED: "#ffa31a",
    Severity.LOW: "#ffff80",
}

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_valid_color(value: object) -> bool:
    """Return True when value is a well-formed #RRGGBB hex color string."""
    return isinstance(value, str) and bool(_HEX_COLOR_RE.match(value))


def validate_color(value: object) -> str:
    """
    Validate a #RRGGBB color and return it normalized to lowercase.

    Raises ValueError on any malformed value so callers (C3 settings save) can
    reject before persisting.
    """
    if not is_valid_color(value):
        raise ValueError("Invalid color {0!r}; expected #RRGGBB".format(value))
    return value.lower()  # type: ignore[union-attr]


# V2 user color tags (SPEC "User Color Tags"). These are visual tags only and
# never participate in severity precedence or hit counts.
COLOR_TAG_NONE = "none"
USER_COLOR_KEYS = ("user1", "user2", "user3")
VALID_COLOR_TAGS = (COLOR_TAG_NONE,) + USER_COLOR_KEYS
DEFAULT_USER_COLORS: Dict[str, str] = {"user1": "", "user2": "", "user3": ""}


def normalize_color_tag(value: object) -> str:
    """Map a stored token to a canonical color tag; unknown values -> 'none'."""
    if isinstance(value, str):
        token = value.strip().lower()
        if token in VALID_COLOR_TAGS:
            return token
    return COLOR_TAG_NONE


def is_valid_user_color(value: object) -> bool:
    """Return True when value is an empty string or a well-formed #RRGGBB color."""
    return value == "" or is_valid_color(value)


def validate_user_color(value: object) -> str:
    """
    Validate a user color and return it normalized.

    Empty string is allowed (visually inactive) and passes through; any non-empty
    value must be a valid #RRGGBB color (normalized lowercase via validate_color).
    Raises ValueError on a malformed non-empty value so the C10 save path can
    reject before persisting.
    """
    if value == "":
        return ""
    return validate_color(value)


@dataclasses.dataclass(frozen=True)
class SherlockPattern:
    """A single match pattern. Frozen so the built-in catalog is never mutated."""

    key: str
    category: str
    label: str
    pattern: str
    severity: Severity
    enabled: bool = True
    builtin: bool = False
    color_tag: str = COLOR_TAG_NONE


# Built-in pattern specs as immutable tuples. builtin_patterns() turns these into
# fresh SherlockPattern objects on every call so callers can never mutate a shared
# module-global list of live pattern objects.
_BUILTIN_SPECS = (
    # (key, category, label, pattern, severity)
    ("cred_password", "Credentials", "Password files", "*password*", Severity.HIGH),
    ("cred_secret", "Credentials", "Secrets", "*secret*", Severity.HIGH),
    ("cred_env", "Credentials", "Env files", "*.env", Severity.HIGH),
    ("cred_credentials", "Credentials", "Credential files", "credentials*", Severity.HIGH),
    ("key_pem", "Private keys", "PEM keys", "*.pem", Severity.HIGH),
    ("key_key", "Private keys", "Key files", "*.key", Severity.HIGH),
    ("key_idrsa", "Private keys", "SSH private keys", "id_rsa*", Severity.HIGH),
    ("key_pfx", "Certificates", "PFX certificates", "*.pfx", Severity.HIGH),
    ("pii_ssn", "PII", "Social security", "*ssn*", Severity.HIGH),
    ("pii_passport", "PII", "Passport", "*passport*", Severity.HIGH),
    ("pii_national_id", "PII", "National ID", "*national_id*", Severity.HIGH),
    ("fin_payroll", "Finance", "Payroll", "*payroll*", Severity.MED),
    ("fin_invoice", "Finance", "Invoices", "*invoice*", Severity.MED),
    ("fin_w2", "Finance", "Tax W-2", "*w2*", Severity.MED),
    ("fin_tax", "Finance", "Tax", "*tax*", Severity.MED),
    ("hr_confidential", "HR/Legal", "Confidential HR", "*confidential_hr*", Severity.MED),
    ("hr_contract", "HR/Legal", "Contracts", "*contract*", Severity.MED),
    ("hr_customer", "Customer data", "Customer data", "*customer*", Severity.MED),
    ("bak_bak", "Backups", "Backup files", "*.bak", Severity.MED),
    ("bak_sql", "Database dumps", "SQL dumps", "*.sql", Severity.MED),
    ("bak_backup", "Backups", "Backups", "*backup*", Severity.MED),
    ("bak_dump", "Database dumps", "Dumps", "*.dump", Severity.MED),
    ("int_internal", "Internal", "Internal labels", "*internal*", Severity.LOW),
    ("int_confidential", "Internal", "Confidential labels", "*confidential*", Severity.LOW),
    ("int_do_not_share", "Internal", "Do-not-share labels", "*do_not_share*", Severity.LOW),
)


def builtin_patterns() -> List[SherlockPattern]:
    """Return a fresh list of built-in patterns (enabled, builtin=True)."""
    return [
        SherlockPattern(
            key=key,
            category=category,
            label=label,
            pattern=pattern,
            severity=severity,
            enabled=True,
            builtin=True,
        )
        for (key, category, label, pattern, severity) in _BUILTIN_SPECS
    ]


@dataclasses.dataclass
class SherlockSettings:
    """Sherlock runtime settings. Persistence/registration belongs to C3."""

    ignore_case: bool = True
    run_after_probe: bool = False
    colors: Dict[Severity, str] = dataclasses.field(default_factory=lambda: dict(DEFAULT_COLORS))
    user_colors: Dict[str, str] = dataclasses.field(
        default_factory=lambda: dict(DEFAULT_USER_COLORS)
    )
    patterns: List[SherlockPattern] = dataclasses.field(default_factory=builtin_patterns)

    def color_for(self, severity: Severity) -> str:
        """Return the configured color for severity, falling back to default."""
        return self.colors.get(severity, DEFAULT_COLORS[severity])

    def user_color_for(self, tag: object) -> str:
        """Return the configured color for a user tag token, or '' if unset.

        Normalizes the tag; 'none', unknown, or unconfigured tags return ''.
        """
        token = normalize_color_tag(tag)
        if token in USER_COLOR_KEYS:
            return self.user_colors.get(token, "")
        return ""


def default_settings() -> SherlockSettings:
    """Build a fresh settings object with default colors and built-in patterns."""
    return SherlockSettings(
        ignore_case=True,
        run_after_probe=False,
        colors=dict(DEFAULT_COLORS),
        user_colors=dict(DEFAULT_USER_COLORS),
        patterns=builtin_patterns(),
    )
