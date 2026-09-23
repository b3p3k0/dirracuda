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


def run_backend_spec(
    run_id: str, *, path: Path | None = None,
) -> tuple[BackendKind, str, bool, str | None, str | None]:
    """Return (kind, endpoint, plaintext_ack, model_id, cert_fingerprint)."""
    conn = open_connection(path, read_only=True)
    try:
        row = conn.execute(
            "SELECT r.backend_kind, r.model_tag, r.identity_kind, "
            "p.scheme, p.host, p.port, p.plaintext_ack, p.cert_fingerprint "
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
        # No profile recorded: the pre-N2b default, unchanged.
        return kind, DEFAULT_ENDPOINT, False, None, None
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


__all__ = ["BackendSelectionError", "backend_for_run", "run_backend_spec"]
