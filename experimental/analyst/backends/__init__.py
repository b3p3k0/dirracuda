"""Backend transports for Analyst.

The registry dispatches by ``BackendKind``. N2a shipped the pure types; both
transports are registered.
"""

from __future__ import annotations

from typing import Any, Callable

from .base import (
    UNKNOWN_CONTEXT,
    BackendCapabilities,
    BackendError,
    BackendKind,
    IdentityKind,
    ModelIdentity,
    cancellation_label,
)
from .ollama import OllamaBackend
from .openai_api import OpenAICompatBackend

#: One factory per transport. Adding a backend is one entry here.
_REGISTRY: dict[BackendKind, Callable[..., Any]] = {
    BackendKind.OLLAMA: OllamaBackend,
    BackendKind.OPENAI_COMPAT: OpenAICompatBackend,
}


def supported_kinds() -> tuple[BackendKind, ...]:
    """Return every backend kind this build can actually talk to."""
    return tuple(_REGISTRY)


def build_backend(kind: BackendKind | str, **kwargs: Any) -> Any:
    """Return a backend for one kind, or refuse a kind this build cannot serve."""
    if not isinstance(kind, BackendKind):
        try:
            kind = BackendKind(kind)
        except ValueError:
            raise BackendError("backend kind is not a known transport") from None
    factory = _REGISTRY.get(kind)
    if factory is None:
        raise BackendError(
            f"the {kind.value} backend is not available in this build yet"
        )
    return factory(**kwargs)


__all__ = [
    "BackendCapabilities",
    "BackendError",
    "BackendKind",
    "IdentityKind",
    "ModelIdentity",
    "OllamaBackend",
    "OpenAICompatBackend",
    "UNKNOWN_CONTEXT",
    "build_backend",
    "cancellation_label",
    "supported_kinds",
]
