"""N1: the endpoint is a supplied value, and every request seal follows it.

Acceptance 1 (byte-identical loopback) and 3 (per-endpoint identity hash) from
`N1_CARD.md` are asserted here.
"""

import hashlib

import pytest

from experimental.analyst import contact_contract as contacts
from experimental.analyst import ollama_contract as contract
from experimental.analyst.contact_contract import ContactKind
from experimental.analyst.endpoint import AddressPolicyError
from experimental.analyst.ollama_client import OllamaClient
from experimental.analyst.ollama_contract import (
    ContractError,
    DiscoveryRequest,
    build_chat_request,
    build_discovery_request,
    build_read_chat_request,
    discovery_request_sha256,
    valid_endpoint,
)

_NONCE = "FENCE_0123456789ABCDEF"
_REMOTE = "http://mimir:9292"
_OTHER = "http://100.125.197.36:9292"


# --------------------------------------------------------------------------
# Acceptance 1 — the loopback build is unchanged
# --------------------------------------------------------------------------

def test_loopback_url_constants_are_byte_identical_to_pre_n1():
    assert contract.OLLAMA_ENDPOINT == "http://127.0.0.1:11434"
    assert contract.OLLAMA_VERSION_URL == "http://127.0.0.1:11434/api/version"
    assert contract.OLLAMA_TAGS_URL == "http://127.0.0.1:11434/api/tags"
    assert contract.OLLAMA_PS_URL == "http://127.0.0.1:11434/api/ps"
    assert contract.OLLAMA_CHAT_URL == "http://127.0.0.1:11434/api/chat"


def test_loopback_discovery_seal_is_unchanged():
    """The published pre-N1 discovery seal, recomputed the N1 way."""
    assert contract.DISCOVERY_REQUEST_SHA256 == discovery_request_sha256()
    assert contract.DISCOVERY_REQUEST_SHA256 == discovery_request_sha256(
        contract.OLLAMA_ENDPOINT
    )


def test_loopback_control_seals_are_unchanged():
    assert contacts.VERSION_REQUEST_SHA256 == contacts.version_request_sha256()
    assert contacts.TAGS_REQUEST_SHA256 == contacts.tags_request_sha256()
    assert contacts.PS_REQUEST_SHA256 == contacts.ps_request_sha256()


def test_the_three_control_seals_stay_distinct():
    seals = {
        contacts.version_request_sha256(_REMOTE),
        contacts.tags_request_sha256(_REMOTE),
        contacts.ps_request_sha256(_REMOTE),
    }
    assert len(seals) == 3


def test_default_chat_request_still_targets_loopback():
    request = build_chat_request("public", nonce=_NONCE)
    assert request.endpoint == contract.OLLAMA_ENDPOINT
    assert request.request_sha256 == hashlib.sha256(request.body).hexdigest()


# --------------------------------------------------------------------------
# Acceptance 3 — two endpoints produce two hashes
# --------------------------------------------------------------------------

def test_two_endpoints_produce_two_discovery_hashes():
    assert discovery_request_sha256(_REMOTE) != discovery_request_sha256()
    assert discovery_request_sha256(_REMOTE) != discovery_request_sha256(_OTHER)


def test_two_endpoints_produce_two_control_hashes():
    for seal in (
        contacts.version_request_sha256,
        contacts.tags_request_sha256,
        contacts.ps_request_sha256,
    ):
        assert seal(_REMOTE) != seal()


def test_discovery_hash_is_stable_for_one_endpoint():
    assert discovery_request_sha256(_REMOTE) == discovery_request_sha256(_REMOTE)


def test_control_request_sha256_refuses_a_kind_with_no_url():
    with pytest.raises(contacts.ContactContractError):
        contacts.control_request_sha256(ContactKind.CHAT)


# --------------------------------------------------------------------------
# Discovery request seals against its own endpoint
# --------------------------------------------------------------------------

def test_build_discovery_request_follows_the_endpoint():
    request = build_discovery_request(_REMOTE)
    assert request.endpoint == _REMOTE
    assert request.url == f"{_REMOTE}/api/tags"
    assert request.request_sha256 == discovery_request_sha256(_REMOTE)


def test_build_discovery_request_normalises_its_input():
    assert build_discovery_request("HTTP://Mimir:9292/").endpoint == _REMOTE


def test_discovery_request_refuses_a_url_from_another_endpoint():
    with pytest.raises(ContractError):
        DiscoveryRequest(
            endpoint=_REMOTE,
            url=contract.OLLAMA_TAGS_URL,
            request_sha256=discovery_request_sha256(_REMOTE),
        )


def test_discovery_request_refuses_a_seal_from_another_endpoint():
    with pytest.raises(ContractError):
        DiscoveryRequest(
            endpoint=_REMOTE,
            url=f"{_REMOTE}/api/tags",
            request_sha256=contract.DISCOVERY_REQUEST_SHA256,
        )


def test_discovery_request_refuses_a_malformed_endpoint():
    with pytest.raises(ContractError):
        DiscoveryRequest(endpoint="mimir:9292")


# --------------------------------------------------------------------------
# Chat builders carry the endpoint
# --------------------------------------------------------------------------

@pytest.mark.parametrize("builder", [build_chat_request, build_read_chat_request])
def test_chat_builders_carry_a_supplied_endpoint(builder):
    request = builder("public", nonce=_NONCE, endpoint=_REMOTE)
    assert request.endpoint == _REMOTE


def test_chat_builder_normalises_a_supplied_endpoint():
    request = build_chat_request("public", nonce=_NONCE, endpoint="HTTP://Mimir:9292/")
    assert request.endpoint == _REMOTE


def test_chat_body_does_not_depend_on_the_endpoint():
    """The endpoint is request identity, not request content."""
    local = build_chat_request("public", nonce=_NONCE)
    remote = build_chat_request("public", nonce=_NONCE, endpoint=_REMOTE)
    assert local.body == remote.body
    assert local.endpoint != remote.endpoint


# --------------------------------------------------------------------------
# valid_endpoint
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value", ["http://127.0.0.1:11434", _REMOTE, "https://mimir:9292"]
)
def test_valid_endpoint_accepts_canonical_forms(value):
    assert valid_endpoint(value) is True


@pytest.mark.parametrize(
    "value",
    ["http://127.0.0.1:11434/", "HTTP://127.0.0.1:11434", "mimir:9292", "", None, 11434],
)
def test_valid_endpoint_refuses_non_canonical_or_malformed(value):
    assert valid_endpoint(value) is False


# --------------------------------------------------------------------------
# D17 in the client
# --------------------------------------------------------------------------

def test_client_defaults_to_loopback():
    assert OllamaClient().endpoint.base_url == contract.OLLAMA_ENDPOINT


def test_client_refuses_a_remote_endpoint_without_acknowledgement():
    """Contract 4.2, enforced at the client rather than in the UI."""
    with pytest.raises(AddressPolicyError):
        OllamaClient(endpoint=_OTHER)


def test_client_accepts_an_acknowledged_private_endpoint():
    assert OllamaClient(
        endpoint=_OTHER, plaintext_ack=True,
    ).endpoint.base_url == _OTHER


def test_client_refuses_a_chat_request_built_for_another_endpoint():
    class _Boom:
        def request(self, *args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("no request may be dispatched")

    session = _Boom()
    session.trust_env = True
    session.max_redirects = 30
    client = OllamaClient(session=session)
    request = build_chat_request("public", nonce=_NONCE, endpoint=_REMOTE)
    result = client.chat(
        request, expected_sha256=request.request_sha256, cancel=lambda: False,
    )
    assert result.status is contract.OllamaStatus.IDENTITY_MISMATCH
