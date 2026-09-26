"""TLS verification and certificate pinning for remote Analyst backends.

Contract 4.4, in order:

1. Verify against the operating system trust store.
2. If that fails and the profile carries a pinned SHA-256 fingerprint, compare
   the presented certificate against it.
3. Otherwise refuse, and offer to pin what was presented.

**There is no "ignore certificate errors" path.** `verify=False` appears
nowhere in this codebase and a guardrail test proves it. A pinned session is
not an unverified session: urllib3 checks the fingerprint with
`hmac.compare_digest`, so a wrong certificate fails the handshake.
"""

from __future__ import annotations

import socket
import ssl
from typing import Any, Final

import requests
from requests.adapters import HTTPAdapter

#: A SHA-256 certificate fingerprint, the only pin length accepted.
FINGERPRINT_CHARS: Final = 64
_HEX: Final = frozenset("0123456789abcdef")
_PROBE_TIMEOUT_SECONDS: Final = 10.0


class TlsPolicyError(ValueError):
    """A TLS value or outcome is outside the transport policy."""


def normalize_fingerprint(value: object) -> str:
    """Return one canonical lowercase SHA-256 fingerprint, or refuse.

    Accepts the colon-separated form tools print, since that is what an
    operator will paste.
    """
    if type(value) is not str:
        raise TlsPolicyError("certificate fingerprint must be a string")
    cleaned = value.strip().lower().replace(":", "").replace(" ", "")
    if len(cleaned) != FINGERPRINT_CHARS or any(c not in _HEX for c in cleaned):
        raise TlsPolicyError(
            "certificate fingerprint must be a SHA-256 hex digest"
        )
    return cleaned


class _PinnedAdapter(HTTPAdapter):
    """An adapter that accepts exactly one certificate.

    urllib3 compares the presented certificate with `hmac.compare_digest`, so
    this is a stricter check than the trust store, not a weaker one: a
    certificate signed by a trusted CA is still refused unless it is *the*
    pinned certificate.
    """

    def __init__(self, fingerprint: str, **kwargs: Any) -> None:
        self._fingerprint = normalize_fingerprint(fingerprint)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        kwargs["assert_fingerprint"] = self._fingerprint
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().proxy_manager_for(*args, **kwargs)


def build_session(
    *, cert_fingerprint: str | None = None, session: Any | None = None,
) -> Any:
    """Return a session that verifies, by trust store or by pin.

    The returned session always verifies. There is no configuration of this
    function that produces an unverified one.
    """
    resolved = session if session is not None else requests.Session()
    resolved.trust_env = False
    resolved.max_redirects = 0
    # Explicit rather than implicit: the default is already True, and saying so
    # means a reader does not have to know that.
    resolved.verify = True
    if cert_fingerprint is not None:
        resolved.mount("https://", _PinnedAdapter(cert_fingerprint))
    return resolved


def fetch_presented_fingerprint(host: str, port: int) -> str:
    """Return the SHA-256 fingerprint a server presents, so it can be offered.

    Contract 4.4 step 3: when verification fails, Analyst offers to pin what
    was presented. Reading that fingerprint needs an unvalidated handshake.

    This function therefore does the one thing the policy forbids elsewhere --
    and is deliberately shaped so it cannot be misused: it sends nothing, reads
    no application data, returns only a digest, and hands back no session,
    socket or context a caller could reuse. Pinning the result is an explicit
    operator decision, taken with the fingerprint in front of them.
    """
    if type(host) is not str or not host:
        raise TlsPolicyError("host must be a nonempty string")
    if type(port) is not int or not 1 <= port <= 65535:
        raise TlsPolicyError("port is outside 1-65535")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(
            (host, port), timeout=_PROBE_TIMEOUT_SECONDS,
        ) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except OSError as exc:
        raise TlsPolicyError(f"could not read a certificate from {host}") from exc
    if not der:
        raise TlsPolicyError(f"{host} presented no certificate")
    import hashlib

    return hashlib.sha256(der).hexdigest()


__all__ = [
    "FINGERPRINT_CHARS",
    "TlsPolicyError",
    "build_session",
    "fetch_presented_fingerprint",
    "normalize_fingerprint",
]
