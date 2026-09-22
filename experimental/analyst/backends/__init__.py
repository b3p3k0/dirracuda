"""Backend transports for Analyst.

N2a ships the pure types only. The registry, the Ollama wrapper and the
OpenAI-compatible adapter arrive with N2b.
"""

from __future__ import annotations

from .base import (
    BackendCapabilities,
    BackendError,
    BackendKind,
    IdentityKind,
    ModelIdentity,
    UNKNOWN_CONTEXT,
)

__all__ = [
    "BackendCapabilities",
    "BackendError",
    "BackendKind",
    "IdentityKind",
    "ModelIdentity",
    "UNKNOWN_CONTEXT",
]
