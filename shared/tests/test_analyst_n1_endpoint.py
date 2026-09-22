"""N1: the pure endpoint module — parsing, classification, URLs, D17 guard."""

import ast
import os

import pytest

from experimental.analyst.endpoint import (
    DEFAULT_ENDPOINT,
    DEFAULT_PORT,
    LOOPBACK_ENDPOINT,
    PRIVATE_CLASSES,
    AddressClass,
    Endpoint,
    EndpointError,
    RemoteNotEnabledError,
    classify_host,
    is_loopback_endpoint,
    normalize_endpoint,
    ollama_urls,
    parse_endpoint,
    require_connectable,
)


# --------------------------------------------------------------------------
# Address classification — one test per class (N1 acceptance 4)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.1.2.3", "::1", "localhost", "LOCALHOST", "::ffff:127.0.0.1"],
)
def test_classify_loopback(host):
    assert classify_host(host) is AddressClass.LOOPBACK


@pytest.mark.parametrize(
    "host",
    ["10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254", "192.168.1.242"],
)
def test_classify_rfc1918_private(host):
    assert classify_host(host) is AddressClass.PRIVATE


@pytest.mark.parametrize(
    "host",
    ["100.64.0.1", "100.125.197.36", "100.91.126.66", "100.127.255.254"],
)
def test_classify_rfc6598_cgnat(host):
    """Tailscale lives here; both probe hosts must classify as CGNAT."""
    assert classify_host(host) is AddressClass.CGNAT


@pytest.mark.parametrize("host", ["fd00::1", "fdff:ffff::abcd"])
def test_classify_unique_local(host):
    assert classify_host(host) is AddressClass.UNIQUE_LOCAL


@pytest.mark.parametrize("host", ["169.254.1.1", "fe80::1"])
def test_classify_link_local(host):
    assert classify_host(host) is AddressClass.LINK_LOCAL


@pytest.mark.parametrize(
    "host",
    ["8.8.8.8", "1.1.1.1", "172.32.0.1", "192.169.0.1", "100.128.0.1", "2606:4700::1"],
)
def test_classify_public(host):
    """Addresses just outside each private block must read as public."""
    assert classify_host(host) is AddressClass.PUBLIC


@pytest.mark.parametrize("host", ["mimir", "example.com", "ollama.internal"])
def test_classify_hostname_is_unresolved_not_public(host):
    """A name is unresolved, never guessed. DNS is an N3 concern."""
    assert classify_host(host) is AddressClass.UNRESOLVED


def test_private_classes_matches_contract_section_4_2():
    assert PRIVATE_CLASSES == {
        AddressClass.LOOPBACK,
        AddressClass.PRIVATE,
        AddressClass.CGNAT,
        AddressClass.UNIQUE_LOCAL,
    }


# --------------------------------------------------------------------------
# Parsing and normalisation
# --------------------------------------------------------------------------

def test_default_endpoint_is_todays_loopback():
    assert DEFAULT_ENDPOINT == "http://127.0.0.1:11434"
    assert LOOPBACK_ENDPOINT.base_url == DEFAULT_ENDPOINT
    assert LOOPBACK_ENDPOINT.is_loopback


def test_parse_round_trips_the_default():
    assert parse_endpoint(DEFAULT_ENDPOINT).base_url == DEFAULT_ENDPOINT


def test_parse_accepts_an_endpoint_instance_unchanged():
    assert parse_endpoint(LOOPBACK_ENDPOINT) is LOOPBACK_ENDPOINT


def test_parse_defaults_the_port():
    assert parse_endpoint("http://127.0.0.1").port == DEFAULT_PORT


def test_parse_strips_a_trailing_slash():
    assert normalize_endpoint("http://127.0.0.1:11434/") == DEFAULT_ENDPOINT


def test_parse_lowercases_scheme_and_host():
    assert normalize_endpoint("HTTP://LocalHost:9292") == "http://localhost:9292"


def test_ipv6_literal_round_trips_with_brackets():
    endpoint = parse_endpoint("http://[::1]:11434")
    assert endpoint.host == "::1"
    assert endpoint.base_url == "http://[::1]:11434"


def test_https_is_accepted():
    assert parse_endpoint("https://mimir:9292").scheme == "https"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "127.0.0.1:11434",
        "ftp://127.0.0.1:11434",
        "http://",
        "http://127.0.0.1:0",
        "http://127.0.0.1:70000",
        "http://127.0.0.1:notaport",
        "http://bad_host!/",
        "http://127.0.0.1:11434/api/chat",
        None,
        11434,
    ],
)
def test_parse_refuses_malformed_endpoints(value):
    with pytest.raises(EndpointError):
        parse_endpoint(value)


def test_endpoint_rejects_an_out_of_range_port():
    with pytest.raises(EndpointError):
        Endpoint(port=0)


def test_endpoint_rejects_a_bool_port():
    with pytest.raises(EndpointError):
        Endpoint(port=True)


# --------------------------------------------------------------------------
# URL construction
# --------------------------------------------------------------------------

def test_urls_match_todays_frozen_loopback_strings():
    urls = ollama_urls(DEFAULT_ENDPOINT)
    assert urls.version == "http://127.0.0.1:11434/api/version"
    assert urls.tags == "http://127.0.0.1:11434/api/tags"
    assert urls.ps == "http://127.0.0.1:11434/api/ps"
    assert urls.chat == "http://127.0.0.1:11434/api/chat"


def test_urls_follow_a_supplied_endpoint():
    urls = ollama_urls("http://mimir:9292")
    assert urls.chat == "http://mimir:9292/api/chat"


def test_endpoint_urls_helper_agrees_with_module_function():
    assert LOOPBACK_ENDPOINT.urls() == ollama_urls(DEFAULT_ENDPOINT)


# --------------------------------------------------------------------------
# D17 guard
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"],
)
def test_require_connectable_allows_loopback(value):
    assert require_connectable(value).is_loopback


@pytest.mark.parametrize(
    "value",
    [
        "http://100.125.197.36:9292",
        "http://192.168.1.242:11434",
        "https://mimir:9292",
        "http://8.8.8.8:11434",
    ],
)
def test_require_connectable_refuses_every_non_loopback_endpoint(value):
    with pytest.raises(RemoteNotEnabledError):
        require_connectable(value)


def test_remote_refusal_is_not_a_generic_endpoint_error_message():
    """D17 wants an explicit result, not something that reads as transport."""
    with pytest.raises(RemoteNotEnabledError) as excinfo:
        require_connectable("http://mimir:9292")
    assert "not enabled yet" in str(excinfo.value)
    assert "mimir" in str(excinfo.value)


def test_remote_not_enabled_is_an_endpoint_error():
    assert issubclass(RemoteNotEnabledError, EndpointError)


def test_is_loopback_endpoint_is_false_for_garbage_not_raising():
    assert is_loopback_endpoint("not-an-endpoint") is False
    assert is_loopback_endpoint(DEFAULT_ENDPOINT) is True


# --------------------------------------------------------------------------
# Purity guardrail — endpoint.py performs no I/O
# --------------------------------------------------------------------------

_MODULE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "experimental",
    "analyst",
    "endpoint.py",
)

_FORBIDDEN_PREFIXES = (
    "sqlite3",
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "pathlib",
    "shutil",
    "subprocess",
    "tkinter",
    "experimental.analyst.db_schema",
    "experimental.analyst.store",
    "experimental.analyst.service",
)

_FORBIDDEN_CALLS = {"open"}


def _endpoint_tree():
    with open(_MODULE, "r", encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=_MODULE)


def test_endpoint_module_imports_no_io_layer():
    names = []
    for node in ast.walk(_endpoint_tree()):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    offenders = [
        name
        for name in names
        if any(name == prefix or name.startswith(prefix + ".")
               for prefix in _FORBIDDEN_PREFIXES)
    ]
    assert offenders == [], f"endpoint.py must stay pure; found {offenders}"


def test_endpoint_module_opens_no_files():
    called = {
        node.func.id
        for node in ast.walk(_endpoint_tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (called & _FORBIDDEN_CALLS)


def test_endpoint_module_resolves_no_names():
    """Classification must never call into DNS. N3 owns resolution."""
    source = open(_MODULE, "r", encoding="utf-8").read()
    for forbidden in ("gethostbyname", "getaddrinfo", "resolve("):
        assert forbidden not in source
