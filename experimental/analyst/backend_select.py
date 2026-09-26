"""Choose the backend a run should use, from the profile it recorded.

A run pins its server at creation (`analyst_runs.profile_id` and
`backend_kind`). This module turns that record back into a live client, so the
worker talks to the server the run was created against rather than to whatever
the default happens to be.

A run with no recorded profile is a pre-N2b run, and gets exactly what it got
before: a loopback Ollama client.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .backends import BackendKind
from .backends.openai_api import OpenAICompatBackend
from .endpoint import DEFAULT_ENDPOINT
from .ollama_client import OllamaClient
from .store import open_connection


class BackendSelectionError(ValueError):
    """A run names a server that cannot be rebuilt."""


class RunPinMismatch(BackendSelectionError):
    """The server a run was created against has changed underneath it.

    Contract 6.4: one report comes from one model. Mixing two destroys the
    grounding claim the whole read-first contract rests on, so a mismatched
    resume is refused and the operator is offered a new run instead.
    """


class ProfileUnreachable(BackendSelectionError):
    """The profile a run was pinned to no longer exists.

    Contract 6.4 holds such a run resumable rather than retargeting it: the
    profile may come back, and silently running it somewhere else would be a
    different claim than the one the report will make.
    """


def run_backend_spec(
    run_id: str, *, path: Path | None = None,
) -> tuple[BackendKind, str, bool, str | None, str | None]:
    """Return (kind, endpoint, plaintext_ack, model_id, cert_fingerprint)."""
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT r.backend_kind, r.model_tag, r.identity_kind, "
            "p.scheme, p.host, p.port, p.plaintext_ack, p.cert_fingerprint, "
            "p.backend_kind AS profile_backend_kind, "
            "r.profile_id AS profile_id_recorded "
            "FROM analyst_runs r "
            "LEFT JOIN analyst_llm_profile p ON p.profile_id = r.profile_id "
            "WHERE r.run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise BackendSelectionError("run does not exist")
    kind_value = row["backend_kind"] or BackendKind.OLLAMA.value
    try:
        kind = BackendKind(kind_value)
    except ValueError:
        raise BackendSelectionError(
            f"run names an unknown backend {kind_value!r}"
        ) from None
    if row["host"] is None:
        if row["profile_id_recorded"] is not None:
            # The run named a profile and that profile is gone. Hold it
            # resumable rather than quietly running it somewhere else.
            raise ProfileUnreachable(
                "the model server this run was created against is no longer "
                "configured. Restore that profile, or start a new run."
            )
        # No profile recorded: the pre-N2b default, unchanged.
        return kind, DEFAULT_ENDPOINT, False, None, None
    if row["profile_backend_kind"] is not None and (
        str(row["profile_backend_kind"]) != kind.value
    ):
        raise RunPinMismatch(
            f"this run was created against a {kind.value} server, but that "
            f"profile is now {row['profile_backend_kind']}. Start a new run."
        )
    endpoint = f"{row['scheme']}://{row['host']}:{int(row['port'])}"
    model_id = row["model_tag"] if row["identity_kind"] == "reported" else None
    return (
        kind, endpoint, bool(row["plaintext_ack"]), model_id,
        row["cert_fingerprint"],
    )


def backend_for_run(run_id: str, *, path: Path | None = None, **kwargs: Any) -> Any:
    """Return a live client for the server this run was created against.

    The address policy is applied inside the client, at construction, so a run
    whose host has since moved outside the permitted ranges fails closed here
    rather than reaching the network (contract 4.3).
    """
    kind, endpoint, plaintext_ack, model_id, pin = run_backend_spec(
        run_id, path=path,
    )
    if kind is BackendKind.OLLAMA:
        # Returned as the raw client, not the thin wrapper, so a loopback run
        # is object-for-object what it was before N2b.
        return OllamaClient(endpoint=endpoint, cert_fingerprint=pin, **kwargs)
    return OpenAICompatBackend(
        endpoint=endpoint,
        plaintext_ack=plaintext_ack,
        model_id=model_id,
        cert_fingerprint=pin,
        **kwargs,
    )


__all__ = [
    "BackendSelectionError",
    "ProfileUnreachable",
    "RunPinMismatch",
    "backend_for_run",
    "run_backend_spec",
]
