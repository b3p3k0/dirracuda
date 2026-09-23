"""Bearer tokens for remote Analyst backends, held in Keymaster.

Contract 9. The token lives in the existing Keymaster store under the
`LLM_SERVER` provider, reusing its AES-GCM encryption and PBKDF2-derived root
key. No second secret store is written, and no new crypto.

A profile with `keymaster_key_id = None` sends no Authorization header. That is
the correct configuration for a loopback Ollama and for an unauthenticated
private-range server reached under contract 4.2.
"""

from __future__ import annotations

from typing import Any

from experimental.keymaster.models import PROVIDER_LLM_SERVER


class CredentialError(ValueError):
    """A stored credential cannot be used for this run."""


class KeymasterLocked(CredentialError):
    """Keymaster holds the token but is locked (contract 9).

    Raised instead of quietly sending no token: a run that should be
    authenticated must fail loudly, not connect anonymously.
    """


def resolve_bearer_token(
    key_id: int | None, *, session_keys: dict[str, bytes] | None = None,
) -> str | None:
    """Return the bearer token a profile names, or None when it names none.

    Never logs the token, never includes it in an exception message, and never
    returns it to a caller that did not ask for this key by id.
    """
    if key_id is None:
        return None
    if type(key_id) is bool or type(key_id) is not int or key_id <= 0:
        raise CredentialError("keymaster key id is invalid")

    from experimental.keymaster import store as keymaster

    conn = keymaster.open_connection()
    try:
        try:
            row = keymaster.get_key(conn, key_id, session_keys=session_keys)
        except Exception as exc:
            # The store raises KeymasterLockedError with exactly the message
            # contract 9 asks for. Re-raise it as ours so callers need not
            # import the sidecar, and so the message cannot pick up detail.
            if type(exc).__name__ in {
                "KeymasterLockedError", "PassphraseRequiredError",
            }:
                raise KeymasterLocked("Keymaster is locked") from None
            raise CredentialError("stored credential could not be read") from None
    finally:
        conn.close()

    if row is None:
        raise CredentialError("the profile names a credential that no longer exists")
    if str(row.get("provider")) != PROVIDER_LLM_SERVER:
        raise CredentialError("the profile names a credential of the wrong kind")
    token = row.get("api_key")
    if type(token) is not str or not token.strip():
        raise KeymasterLocked("Keymaster is locked")
    return token


__all__ = ["CredentialError", "KeymasterLocked", "resolve_bearer_token"]
