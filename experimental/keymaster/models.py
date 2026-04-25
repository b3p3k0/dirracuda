"""Data models and constants for the Keymaster sidecar module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PROVIDER_SHODAN = "SHODAN"
PROVIDERS = (PROVIDER_SHODAN,)


@dataclass(frozen=True)
class KeymasterKey:
    """Keymaster key row model."""

    key_id: int
    provider: str
    label: str
    api_key: str
    api_key_normalized: str
    notes: str
    created_at: str
    updated_at: str
    last_used_at: Optional[str]


class KeymasterError(Exception):
    """Base Keymaster exception."""


class DuplicateKeyError(KeymasterError):
    """Raised when an entry duplicates an api_key value within a provider."""
