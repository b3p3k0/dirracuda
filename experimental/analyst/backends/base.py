"""Pure backend types shared by every Analyst transport.

No network, no database, no filesystem, no Tk. A guardrail test enforces it,
mirroring ``shared/tests/test_sherlock_purity.py`` and the one on
``experimental/analyst/endpoint.py``.

Contract: `CONTRACT_REMOTE_BACKENDS.md` sections 3 and 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


#: A context size the server did not report. Never treated as unlimited
#: (contract 5.3): an unknown context skips the preflight refusal and the UI
#: says the limit could not be determined.
UNKNOWN_CONTEXT: Final = 0


class BackendError(ValueError):
    """A backend value is outside the remote-backends contract."""


class BackendKind(str, Enum):
    """The two transports of contract section 3."""

    OLLAMA = "ollama"
    OPENAI_COMPAT = "openai"


class IdentityKind(str, Enum):
    """How strongly a run's model identity is established (contract 6.1)."""

    #: Verified by a SHA-256 model digest.
    DIGEST = "digest"
    #: What the server said about itself. Never presented as verified.
    REPORTED = "reported"


def cancellation_label(kind: BackendKind) -> str:
    """Return how honestly a cancellation may be described (contract 7.3).

    Measured: killing the client freed the llama.cpp slot within 4 seconds, so
    on that backend Analyst may say "cancelled" plainly. Ollama's /api/ps is
    skewed by keep_alive and cannot prove a stop, so its hedged wording stands.
    """
    if kind is BackendKind.OPENAI_COMPAT:
        return "cancelled"
    return "cancel requested; server completion unverified"


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """What one server will actually honour."""

    max_context: int
    can_set_context_per_request: bool
    supports_json_schema: bool
    supports_seed: bool
    server_version: str | None = None

    def __post_init__(self) -> None:
        if type(self.max_context) is not int or self.max_context < 0:
            raise BackendError("max context must be a nonnegative integer")
        for value in (
            self.can_set_context_per_request,
            self.supports_json_schema,
            self.supports_seed,
        ):
            if type(value) is not bool:
                raise BackendError("capability flags must be booleans")
        if self.server_version is not None and (
            type(self.server_version) is not str or not self.server_version
        ):
            raise BackendError("server version must be a nonempty string or None")

    @property
    def context_is_known(self) -> bool:
        """Return whether the server reported a usable context size."""
        return self.max_context > UNKNOWN_CONTEXT

    def admits(self, required_context: int) -> bool:
        """Return whether a run needing this much context may proceed.

        An unknown context admits the run: contract 5.4 makes the server the
        guarantee, and the preflight gate is fail-fast UX rather than the only
        line of defence.
        """
        if type(required_context) is not int or required_context <= 0:
            raise BackendError("required context must be a positive integer")
        return not self.context_is_known or self.max_context >= required_context


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """One run's model identity, always labelled by kind (contract 6.1)."""

    kind: IdentityKind
    model_name: str
    digest: str | None = None
    model_path: str | None = None
    n_params: int | None = None
    size_bytes: int | None = None
    ftype: str | None = None
    n_vocab: int | None = None
    n_ctx: int | None = None
    n_ctx_train: int | None = None
    server_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IdentityKind):
            raise BackendError("identity kind is not a closed kind")
        if type(self.model_name) is not str or not self.model_name:
            raise BackendError("model name must be a nonempty string")
        if self.kind is IdentityKind.DIGEST:
            if (
                type(self.digest) is not str
                or len(self.digest) != 64
                or any(char not in "0123456789abcdef" for char in self.digest)
            ):
                raise BackendError("a digest identity needs one lowercase SHA-256")
        elif self.digest is not None:
            raise BackendError("a reported identity must carry no digest")
        for value in (
            self.model_path, self.ftype, self.server_fingerprint,
        ):
            if value is not None and (type(value) is not str or not value):
                raise BackendError("reported identity text must be nonempty or None")
        for value in (
            self.n_params, self.size_bytes, self.n_vocab,
            self.n_ctx, self.n_ctx_train,
        ):
            if value is not None and (type(value) is not int or value <= 0):
                raise BackendError("reported identity counts must be positive or None")

    @property
    def is_verified(self) -> bool:
        """Return whether a cryptographic digest backs this identity."""
        return self.kind is IdentityKind.DIGEST

    def as_run_columns(self) -> dict[str, object]:
        """Return this identity as the ``analyst_runs`` columns of schema v8."""
        return {
            "model_tag": self.model_name,
            "model_digest": self.digest,
            "identity_kind": self.kind.value,
            "model_path": self.model_path,
            "model_n_params": self.n_params,
            "model_size_bytes": self.size_bytes,
            "model_ftype": self.ftype,
            "model_n_vocab": self.n_vocab,
            "model_n_ctx": self.n_ctx,
            "model_n_ctx_train": self.n_ctx_train,
            "server_fingerprint": self.server_fingerprint,
        }


__all__ = [
    "BackendCapabilities",
    "BackendError",
    "BackendKind",
    "IdentityKind",
    "ModelIdentity",
    "UNKNOWN_CONTEXT",
    "cancellation_label",
]
