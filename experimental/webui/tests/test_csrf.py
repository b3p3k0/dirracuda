"""Unit tests for validate_csrf and same_origin."""

from unittest.mock import MagicMock

from experimental.webui.dependencies import validate_csrf, same_origin


def _req(headers=None, scheme="http"):
    """Build a minimal mock FastAPI Request."""
    req = MagicMock()
    req.headers = headers or {"host": "localhost:5480"}
    req.url.scheme = scheme
    return req


# --- validate_csrf ---

def test_csrf_valid():
    assert validate_csrf("abc123", "abc123") is True


def test_csrf_mismatch():
    assert validate_csrf("wrong", "abc123") is False


def test_csrf_none():
    assert validate_csrf(None, "abc123") is False


def test_csrf_empty_string():
    assert validate_csrf("", "abc123") is False


# --- same_origin: Origin header ---

def test_same_origin_matching_origin():
    req = _req({"host": "localhost:5480", "origin": "http://localhost:5480"})
    assert same_origin(req) is True


def test_same_origin_mismatched_origin():
    req = _req({"host": "localhost:5480", "origin": "http://attacker.com"})
    assert same_origin(req) is False


def test_same_origin_origin_lookalike():
    # Exact netloc comparison must reject subdomain tricks.
    req = _req({"host": "example.org", "origin": "http://example.org.attacker.com"})
    assert same_origin(req) is False


def test_same_origin_origin_scheme_mismatch():
    req = _req({"host": "localhost:5480", "origin": "https://localhost:5480"}, scheme="http")
    assert same_origin(req) is False


# --- same_origin: Referer header (netloc-only, intentional) ---

def test_same_origin_matching_referer():
    req = _req({"host": "localhost:5480", "referer": "http://localhost:5480/login"})
    assert same_origin(req) is True


def test_same_origin_referer_https_same_host():
    # Referer scheme is intentionally not checked for localhost/dev flexibility.
    req = _req({"host": "localhost:5480", "referer": "https://localhost:5480/login"})
    assert same_origin(req) is True


def test_same_origin_mismatched_referer():
    req = _req({"host": "localhost:5480", "referer": "http://attacker.com/evil"})
    assert same_origin(req) is False


def test_same_origin_referer_prefix_lookalike():
    # Exact netloc match must reject prefix-lookalike domains.
    req = _req({"host": "example.org", "referer": "http://example.org.attacker.com/page"})
    assert same_origin(req) is False


# --- same_origin: absent headers ---

def test_same_origin_no_headers():
    req = _req({"host": "localhost:5480"})
    assert same_origin(req) is True
