"""N3: TLS verification and certificate pinning (contract 4.4)."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from experimental.analyst.tls import (
    FINGERPRINT_CHARS,
    TlsPolicyError,
    build_session,
    normalize_fingerprint,
)

_PIN = "ab" * 32


# --------------------------------------------------------------------------
# Fingerprints
# --------------------------------------------------------------------------

def test_a_plain_hex_fingerprint_is_accepted():
    assert normalize_fingerprint(_PIN) == _PIN


def test_the_colon_form_an_operator_pastes_is_accepted():
    colons = ":".join(_PIN[i:i + 2] for i in range(0, len(_PIN), 2))
    assert normalize_fingerprint(colons.upper()) == _PIN


@pytest.mark.parametrize(
    "bad", ["", "xyz", "ab" * 31, "ab" * 33, "zz" * 32, None, 42, b"ab" * 32]
)
def test_a_malformed_fingerprint_is_refused(bad):
    with pytest.raises(TlsPolicyError):
        normalize_fingerprint(bad)


def test_only_sha256_length_is_accepted():
    """A SHA-1 pin is not admitted, however it is written."""
    assert FINGERPRINT_CHARS == 64
    with pytest.raises(TlsPolicyError):
        normalize_fingerprint("ab" * 20)


# --------------------------------------------------------------------------
# Sessions always verify
# --------------------------------------------------------------------------

def test_a_plain_session_verifies_against_the_trust_store():
    session = build_session()
    assert session.verify is True
    assert session.trust_env is False
    assert session.max_redirects == 0


def test_a_pinned_session_still_verifies():
    """Pinning is stricter than the trust store, not a way around it."""
    session = build_session(cert_fingerprint=_PIN)
    assert session.verify is True
    adapter = session.adapters["https://"]
    assert type(adapter).__name__ == "_PinnedAdapter"


def test_the_pin_reaches_the_pool_manager():
    captured = {}

    class _Probe(dict):
        def __setitem__(self, key, value):
            captured[key] = value
            super().__setitem__(key, value)

    session = build_session(cert_fingerprint=_PIN)
    adapter = session.adapters["https://"]
    kwargs: dict = {}
    try:
        adapter.init_poolmanager(1, 1, **kwargs)
    except Exception:
        pass
    assert adapter._fingerprint == _PIN


def test_a_malformed_pin_is_refused_at_session_build():
    with pytest.raises(TlsPolicyError):
        build_session(cert_fingerprint="not-a-fingerprint")


# --------------------------------------------------------------------------
# The guardrail: no "ignore certificate errors" path exists
# --------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_SCANNED = (
    _ROOT / "experimental" / "analyst",
    _ROOT / "gui",
    _ROOT / "shared",
)


def _python_files():
    for root in _SCANNED:
        for path in root.rglob("*.py"):
            if "/tests/" in str(path) or path.name.startswith("test_"):
                continue
            yield path


def test_verify_false_appears_nowhere():
    """Contract 4.4: no code path may disable certificate verification.

    Mirrors test_messagebox_guardrail.py: the rule is enforced, not trusted.
    """
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "verify":
                value = node.value
                if isinstance(value, ast.Constant) and value.value is False:
                    offenders.append(f"{path}:{value.lineno}")
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "verify"
                        and isinstance(node.value, ast.Constant)
                        and node.value.value is False
                    ):
                        offenders.append(f"{path}:{node.lineno}")
    assert offenders == [], f"certificate verification is disabled at {offenders}"


def test_analyst_has_exactly_one_unvalidated_handshake():
    """Within Analyst, CERT_NONE exists only in the pin probe.

    Scoped to `experimental/analyst/` deliberately. The scanner
    (`shared/http_browser.py`, `gui/utils/protocol_extract_runner.py`) also
    uses CERT_NONE, behind an explicit allow_insecure_tls flag, because
    browsing an arbitrary open directory on an untrusted host is the product's
    purpose -- validating certificates there would defeat it. Contract 4.4
    governs what Analyst sends its own harvested text over, not what the
    scanner reads from strangers.
    """
    analyst = _ROOT / "experimental" / "analyst"
    users = sorted(
        path.name
        for path in analyst.rglob("*.py")
        if "CERT_NONE" in path.read_text(encoding="utf-8")
    )
    assert users == ["tls.py"], f"CERT_NONE is used outside tls.py: {users}"


def test_the_pin_probe_returns_only_a_digest():
    """It must hand back no session, socket or context a caller could reuse."""
    import inspect

    from experimental.analyst import tls

    source = inspect.getsource(tls.fetch_presented_fingerprint)
    assert "return hashlib.sha256(der).hexdigest()" in source
    assert source.count("return ") == 1
