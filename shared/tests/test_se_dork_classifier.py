"""
Unit tests for experimental.se_dork.classifier.

try_http_request is monkeypatched throughout; no network access.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.se_dork.classifier import (
    ClassifyResult,
    VERDICT_OPEN_INDEX,
    VERDICT_MAYBE,
    VERDICT_NOISE,
    VERDICT_ERROR,
    classify_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_INDEX_HTML = (
    "<html><head><title>Index of /</title></head>"
    "<body><a href='file.txt'>file.txt</a></body></html>"
)
_PLAIN_HTML = "<html><body><p>hello</p></body></html>"


def _mock_request(monkeypatch, status: int, body: str = "", reason: str = ""):
    """Patch try_http_request to return a fixed response."""
    monkeypatch.setattr(
        "experimental.se_dork.classifier.try_http_request",
        lambda *args, **kwargs: (status, body, False, reason),
    )


# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------

def test_classify_open_index(monkeypatch):
    _mock_request(monkeypatch, 200, _VALID_INDEX_HTML)
    r = classify_url("http://192.168.1.1/")
    assert r.verdict == VERDICT_OPEN_INDEX
    assert r.reason_code is None
    assert r.http_status == 200


def test_classify_maybe_200_no_index(monkeypatch):
    _mock_request(monkeypatch, 200, _PLAIN_HTML)
    r = classify_url("http://192.168.1.1/")
    assert r.verdict == VERDICT_MAYBE
    assert r.reason_code == "no_index_tag"
    assert r.http_status == 200


def test_classify_noise_404(monkeypatch):
    _mock_request(monkeypatch, 404)
    r = classify_url("http://192.168.1.1/missing")
    assert r.verdict == VERDICT_NOISE
    assert r.reason_code == "http_404"
    assert r.http_status == 404


def test_classify_noise_500(monkeypatch):
    _mock_request(monkeypatch, 500)
    r = classify_url("http://192.168.1.1/")
    assert r.verdict == VERDICT_NOISE
    assert r.reason_code == "http_500"


def test_classify_redirect_code(monkeypatch):
    _mock_request(monkeypatch, 301)
    r = classify_url("http://192.168.1.1/")
    assert r.verdict == VERDICT_MAYBE
    assert r.reason_code == "http_301"
    assert r.http_status == 301


# ---------------------------------------------------------------------------
# Network-level failures → ERROR
# ---------------------------------------------------------------------------

def test_classify_error_timeout(monkeypatch):
    _mock_request(monkeypatch, 0, reason="timeout")
    r = classify_url("http://192.168.1.1/")
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "timeout"
    assert r.http_status is None


def test_classify_error_dns_fail(monkeypatch):
    _mock_request(monkeypatch, 0, reason="dns_fail")
    r = classify_url("http://noexist.example.com/")
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "dns_fail"


def test_classify_error_connect_fail(monkeypatch):
    _mock_request(monkeypatch, 0, reason="connect_fail")
    r = classify_url("http://192.168.1.1:9999/")
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "connect_fail"


# ---------------------------------------------------------------------------
# Pre-network rejections (no HTTP call)
# ---------------------------------------------------------------------------

def test_classify_noise_unsupported_scheme(monkeypatch):
    called = []
    monkeypatch.setattr(
        "experimental.se_dork.classifier.try_http_request",
        lambda *a, **kw: called.append(True) or (200, "", False, ""),
    )
    r = classify_url("ftp://192.168.1.1/")
    assert r.verdict == VERDICT_NOISE
    assert r.reason_code == "unsupported_scheme"
    assert called == []  # no HTTP call made


def test_classify_error_no_host(monkeypatch):
    called = []
    monkeypatch.setattr(
        "experimental.se_dork.classifier.try_http_request",
        lambda *a, **kw: called.append(True) or (200, "", False, ""),
    )
    r = classify_url("http:///path")
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "no_host"
    assert called == []


def test_classify_error_parse_error():
    # Non-string input triggers isinstance guard before urlparse is called.
    r = classify_url(123)
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "parse_error"
    assert r.http_status is None


def test_classify_error_parse_error_none():
    r = classify_url(None)  # type: ignore[arg-type]
    assert r.verdict == VERDICT_ERROR
    assert r.reason_code == "parse_error"


# ---------------------------------------------------------------------------
# Port resolution
# ---------------------------------------------------------------------------

def test_classify_port_default_http(monkeypatch):
    received = {}
    def _capture(ip, port, scheme, **kwargs):
        received["port"] = port
        return (200, _PLAIN_HTML, False, "")
    monkeypatch.setattr("experimental.se_dork.classifier.try_http_request", _capture)
    classify_url("http://192.168.1.1/")
    assert received["port"] == 80


def test_classify_port_default_https(monkeypatch):
    received = {}
    def _capture(ip, port, scheme, **kwargs):
        received["port"] = port
        return (200, _PLAIN_HTML, False, "")
    monkeypatch.setattr("experimental.se_dork.classifier.try_http_request", _capture)
    classify_url("https://192.168.1.1/")
    assert received["port"] == 443


def test_classify_port_explicit(monkeypatch):
    received = {}
    def _capture(ip, port, scheme, **kwargs):
        received["port"] = port
        return (200, _PLAIN_HTML, False, "")
    monkeypatch.setattr("experimental.se_dork.classifier.try_http_request", _capture)
    classify_url("http://192.168.1.1:8080/data")
    assert received["port"] == 8080


# ---------------------------------------------------------------------------
# Verifier call field validation
# ---------------------------------------------------------------------------

def test_classify_url_calls_verifier_with_parsed_fields(monkeypatch):
    """Verify ip, scheme, port, path, request_host are passed correctly."""
    received = {}
    def _capture(ip, port, scheme, allow_insecure_tls, timeout, path, request_host):
        received.update(
            ip=ip, port=port, scheme=scheme, path=path, request_host=request_host
        )
        return (200, _PLAIN_HTML, False, "")
    monkeypatch.setattr("experimental.se_dork.classifier.try_http_request", _capture)

    classify_url("https://myhost.local:9443/some/path")

    assert received["ip"] == "myhost.local"
    assert received["port"] == 9443
    assert received["scheme"] == "https"
    assert received["path"] == "/some/path"
    assert received["request_host"] == "myhost.local"
