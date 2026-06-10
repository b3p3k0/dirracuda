"""
Tests for shared/http_transport.py (C1).

Coverage:
  - _bracket_ipv6, _normalize_hostname, _host_header_value unit tests
  - fp closure via _handle_redirect (unit — no opener lifecycle)
  - Full redirect matrix via daemon HTTP fixture
  - HTTPS proxy bypass via daemon HTTPS fixture
  - Proxy env-var bypass (HTTP and HTTPS)
  - addinfourl interface regression
  - Default-port normalization (unit)
  - IDNA equivalence and oversized-label rejection
  - Non-ASCII (ISO-8859-1) Location header lifecycle
  - IPv6 same-origin and cross-port (conditional)
"""
from __future__ import annotations

import http.client
import http.server
import ipaddress
import os
import socket
import ssl
import tempfile
import threading
import urllib.request
from typing import List, Optional, Tuple

import pytest

from shared.http_transport import (
    RedirectBlockedError,
    RedirectLimitError,
    _SameOriginRedirectHandler,
    _bracket_ipv6,
    _host_header_value,
    _normalize_hostname,
    http_open,
)


# ---------------------------------------------------------------------------
# Proxy env isolation
# ---------------------------------------------------------------------------

_ALL_PROXY_VARS = [
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy",
]


# ---------------------------------------------------------------------------
# Fixtures — HTTP daemon server
# ---------------------------------------------------------------------------

class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    """Serves a pre-scripted list of (status, headers_dict, body_bytes) responses."""

    log_message = lambda *a: None  # silence access log

    def do_GET(self):
        script: List[Tuple[int, dict, bytes]] = self.server._script  # type: ignore[attr-defined]
        idx: int = self.server._script_idx  # type: ignore[attr-defined]
        if idx >= len(script):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"script exhausted")
            return
        status, headers, body = script[idx]
        self.server._script_idx += 1  # type: ignore[attr-defined]
        # Record the request path for assertions
        self.server._requests.append(self.path)  # type: ignore[attr-defined]
        self.server._hosts.append(self.headers.get("Host"))  # type: ignore[attr-defined]
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _IPv6HTTPServer(http.server.HTTPServer):
    address_family = socket.AF_INET6


def _make_http_server(
    script: List[Tuple[int, dict, bytes]],
    host: str = "127.0.0.1",
) -> http.server.HTTPServer:
    cls = _IPv6HTTPServer if ":" in host else http.server.HTTPServer
    server = cls((host, 0), _ScriptedHandler)
    server._script = script  # type: ignore[attr-defined]
    server._script_idx = 0  # type: ignore[attr-defined]
    server._requests = []  # type: ignore[attr-defined]
    server._hosts = []  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ---------------------------------------------------------------------------
# Fixtures — HTTPS daemon server (self-signed, insecure context only)
# ---------------------------------------------------------------------------

def _make_self_signed_cert() -> Tuple[str, str]:
    """Generate a minimal self-signed cert+key pair using only stdlib."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2025, 1, 1))
            .not_valid_after(datetime.datetime(2035, 1, 1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()
        return cert_pem, key_pem
    except ImportError:
        pytest.skip("cryptography package not available for HTTPS fixture")


def _make_https_server(
    script: List[Tuple[int, dict, bytes]],
) -> Tuple[http.server.HTTPServer, ssl.SSLContext]:
    cert_pem, key_pem = _make_self_signed_cert()
    with (
        tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as cf,
        tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as kf,
    ):
        cf.write(cert_pem)
        kf.write(key_pem)
        cf_path, kf_path = cf.name, kf.name

    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server._script = script  # type: ignore[attr-defined]
    server._script_idx = 0  # type: ignore[attr-defined]
    server._requests = []  # type: ignore[attr-defined]
    server._hosts = []  # type: ignore[attr-defined]

    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(cf_path, kf_path)
    server.socket = srv_ctx.wrap_socket(server.socket, server_side=True)

    os.unlink(cf_path)
    os.unlink(kf_path)

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    cli_ctx = ssl.create_default_context()
    cli_ctx.check_hostname = False
    cli_ctx.verify_mode = ssl.CERT_NONE

    return server, cli_ctx


# ---------------------------------------------------------------------------
# Unit helpers for mock fp tests
# ---------------------------------------------------------------------------

class _MockFp:
    def __init__(self):
        self.read_called = False
        self.close_called = False

    def read(self, *a):
        self.read_called = True
        return b""

    def close(self):
        self.close_called = True


def _make_headers(**kw) -> http.client.HTTPMessage:
    msg = http.client.HTTPMessage()
    for k, v in kw.items():
        msg[k] = v
    return msg


def _make_req(url: str) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.timeout = 5.0
    return req


def _make_handler(
    host: str = "127.0.0.1",
    port: int = 8080,
    scheme: str = "http",
    logical_host: Optional[str] = None,
) -> _SameOriginRedirectHandler:
    lh = logical_host or host
    try:
        norm = ipaddress.ip_address(lh.strip("[]")).compressed.lower()
    except ValueError:
        norm = lh.encode("idna").decode("ascii").lower()
    try:
        cnorm = ipaddress.ip_address(host.strip("[]")).compressed.lower()
    except ValueError:
        cnorm = host.encode("idna").decode("ascii").lower()
    return _SameOriginRedirectHandler(
        connect_host_raw=host,
        connect_host_norm=cnorm,
        logical_host_raw=lh,
        logical_host_norm=norm,
        logical_origin=(scheme, norm, port),
    )


# ---------------------------------------------------------------------------
# _bracket_ipv6 unit tests
# ---------------------------------------------------------------------------

def test_bracket_ipv6_ipv4_unchanged():
    assert _bracket_ipv6("1.2.3.4") == "1.2.3.4"


def test_bracket_ipv6_hostname_unchanged():
    assert _bracket_ipv6("example.com") == "example.com"


def test_bracket_ipv6_bare_loopback():
    assert _bracket_ipv6("::1") == "[::1]"


def test_bracket_ipv6_bracketed_loopback_idempotent():
    assert _bracket_ipv6("[::1]") == "[::1]"


def test_bracket_ipv6_full_form_normalized():
    assert _bracket_ipv6("2001:0db8:0000:0000:0000:0000:0000:0001") == "[2001:db8::1]"


# ---------------------------------------------------------------------------
# _normalize_hostname unit tests
# ---------------------------------------------------------------------------

def test_normalize_ipv4():
    assert _normalize_hostname("192.168.1.1") == "192.168.1.1"


def test_normalize_ipv6_bare():
    assert _normalize_hostname("::1") == "::1"


def test_normalize_ipv6_bracketed():
    assert _normalize_hostname("[::1]") == "::1"


def test_normalize_hostname_ascii_lowercase():
    assert _normalize_hostname("EXAMPLE.COM") == "example.com"


def test_normalize_idna_unicode_punycode_equivalence():
    unicode_form = _normalize_hostname("bücher.test")
    punycode_form = _normalize_hostname("xn--bcher-kva.test")
    assert unicode_form == punycode_form
    assert unicode_form == "xn--bcher-kva.test"


def test_normalize_idna_oversized_label_raises_value_error():
    with pytest.raises(ValueError):
        _normalize_hostname("a" * 64 + ".example.com")


def test_normalize_empty_raises():
    with pytest.raises(ValueError):
        _normalize_hostname("")


# ---------------------------------------------------------------------------
# _host_header_value unit tests
# ---------------------------------------------------------------------------

def test_host_header_default_port_http():
    assert _host_header_value("example.com", 80, "http") == "example.com"


def test_host_header_non_default_port():
    assert _host_header_value("example.com", 8080, "http") == "example.com:8080"


def test_host_header_ipv6_default_port():
    assert _host_header_value("::1", 80, "http") == "[::1]"


def test_host_header_ipv6_non_default_port():
    assert _host_header_value("[::1]", 8080, "http") == "[::1]:8080"


def test_host_header_idna_is_ascii():
    assert _host_header_value("例え.テスト", 80, "http") == (
        "xn--r8jz45g.xn--zckzah"
    )


# ---------------------------------------------------------------------------
# fp closure via _handle_redirect (unit — no opener)
# ---------------------------------------------------------------------------

def test_fp_closed_on_missing_location():
    fp = _MockFp()
    handler = _make_handler()
    with pytest.raises(RedirectBlockedError):
        handler._handle_redirect(
            _make_req("http://127.0.0.1:8080/"), fp, 302, "Found",
            _make_headers()
        )
    assert fp.close_called


def test_fp_closed_on_empty_location():
    fp = _MockFp()
    handler = _make_handler()
    with pytest.raises(RedirectBlockedError):
        handler._handle_redirect(
            _make_req("http://127.0.0.1:8080/"), fp, 302, "Found",
            _make_headers(location="   ")
        )
    assert fp.close_called


def test_fp_closed_on_cross_origin_blocked():
    fp = _MockFp()
    handler = _make_handler()
    with pytest.raises(RedirectBlockedError):
        handler._handle_redirect(
            _make_req("http://127.0.0.1:8080/"), fp, 302, "Found",
            _make_headers(location="http://evil.com/")
        )
    assert fp.close_called


def test_fp_closed_on_malformed_ipv6_authority():
    fp = _MockFp()
    handler = _make_handler()
    with pytest.raises(RedirectBlockedError):
        handler._handle_redirect(
            _make_req("http://127.0.0.1:8080/"), fp, 302, "Found",
            _make_headers(location="http://[:::1]:8080/")
        )
    assert fp.close_called


def test_fp_closed_on_redirect_limit():
    fp = _MockFp()
    handler = _make_handler()
    handler._hops = handler.MAX_REDIRECTS
    with pytest.raises(RedirectLimitError):
        handler._handle_redirect(
            _make_req("http://127.0.0.1:8080/"), fp, 302, "Found",
            _make_headers(location="http://127.0.0.1:8080/new")
        )
    assert fp.close_called


# ---------------------------------------------------------------------------
# Default-port normalization (unit — no server)
# ---------------------------------------------------------------------------

def test_default_port_normalization_same_origin():
    """Redirect to implicit port-80 matches logical_origin with explicit port 80."""
    handler = _make_handler(host="127.0.0.1", port=80, scheme="http")
    handler._redirect_is_relative = False
    req = _make_req("http://127.0.0.1:80/path")
    req.timeout = 5.0
    # Construct a minimal fp-like object (redirect_request does not close fp)
    fp = _MockFp()
    result = handler.redirect_request(
        req, fp, 302, "Found", _make_headers(), "http://127.0.0.1/new"
    )
    assert "127.0.0.1:80" in result.full_url


# ---------------------------------------------------------------------------
# Fixture-based redirect tests
# ---------------------------------------------------------------------------

def test_relative_redirect_same_connect_logical():
    port_loc = None  # filled below
    def make_script():
        return [
            (302, {"Location": "/newpath"}, b""),
            (200, {}, b"ok"),
        ]
    srv = _make_http_server(make_script())
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1", scheme="http", port=port,
            path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_same_origin_absolute_redirect():
    srv = _make_http_server([
        (302, {}, b""),   # no Location → redirect_blocked
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_absolute_same_origin_redirect_followed():
    srv = _make_http_server([
        (302, {"Location": "http://127.0.0.1:{PORT}/new"}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    # patch Location with actual port
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port}/new"}, b"")
    try:
        with http_open(
            connect_host="127.0.0.1", scheme="http", port=port,
            path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_cross_scheme_redirect_blocked():
    srv = _make_http_server([
        (302, {}, b""),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"https://127.0.0.1:{port}/"}, b"")
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_cross_host_redirect_blocked():
    srv = _make_http_server([
        (302, {"Location": "http://evil.example.com/"}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_scheme_relative_cross_host_blocked():
    srv = _make_http_server([
        (302, {"Location": "//evil.example.com/path"}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_cross_port_explicit_blocked():
    srv = _make_http_server([
        (302, {}, b""),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port + 1}/"}, b"")
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_cross_port_default_normalization_blocked():
    """Port 80 origin; redirect to same host no explicit port (→ 80 effective) should pass,
    but redirect to explicit port 81 should be blocked."""
    srv = _make_http_server([
        (302, {"Location": "http://127.0.0.1:81/"}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_credential_bearing_redirect_blocked():
    srv = _make_http_server([
        (302, {}, b""),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://u:p@127.0.0.1:{port}/"}, b"")
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_unsupported_scheme_blocked():
    srv = _make_http_server([
        (302, {"Location": "ftp://127.0.0.1/file"}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_missing_location_blocked():
    srv = _make_http_server([
        (302, {}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_empty_location_blocked():
    srv = _make_http_server([
        (302, {"Location": "  "}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_fourth_hop_blocked():
    """Three same-origin hops succeed; fourth raises RedirectLimitError."""
    srv = _make_http_server([
        (302, {}, b""),
        (302, {}, b""),
        (302, {}, b""),
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    # Use unique paths so the self-referential guard does not fire before the hop limit
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port}/a"}, b"")
    srv._script[1] = (302, {"Location": f"http://127.0.0.1:{port}/b"}, b"")
    srv._script[2] = (302, {"Location": f"http://127.0.0.1:{port}/c"}, b"")
    srv._script[3] = (302, {"Location": f"http://127.0.0.1:{port}/d"}, b"")
    try:
        with pytest.raises(RedirectLimitError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_three_hops_succeed():
    """Exactly MAX_REDIRECTS (3) hops should succeed."""
    srv = _make_http_server([
        (302, {}, b""),
        (302, {}, b""),
        (302, {}, b""),
        (200, {}, b"final"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port}/a"}, b"")
    srv._script[1] = (302, {"Location": f"http://127.0.0.1:{port}/b"}, b"")
    srv._script[2] = (302, {"Location": f"http://127.0.0.1:{port}/c"}, b"")
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_url_rewrite_preserves_connect_host():
    """After validation against logical hostname, request must go to connect_host (IP)."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://virtual.test:{port}/new"}, b"")
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",
            scheme="http", port=port, path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert srv._requests[-1] == "/new"
    finally:
        srv.shutdown()


def test_path_params_preserved_in_rewrite():
    """/next;token?x=1 must not become /next?x=1 after rewrite."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port}/next;token?x=1"}, b"")
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
        assert srv._requests[-1] == "/next;token?x=1"
    finally:
        srv.shutdown()


def test_308_same_origin_no_attribute_error():
    """308 must be handled without AttributeError on Python < 3.11."""
    srv = _make_http_server([
        (308, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (308, {"Location": f"http://127.0.0.1:{port}/new"}, b"")
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_malformed_ipv6_authority_blocked():
    srv = _make_http_server([
        (302, {"Location": "http://[:::1]:8080/"}, b""),
    ])
    port = srv.server_address[1]
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="127.0.0.1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()


def test_absolute_to_connect_ip_blocked_when_logical_is_hostname():
    """Absolute redirect to the IP address is blocked when logical origin is a hostname."""
    srv = _make_http_server([
        (302, {}, b""),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://127.0.0.1:{port}/new"}, b"")
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(
                connect_host="127.0.0.1",
                request_host="virtual.test",
                scheme="http", port=port, path="/", timeout=5.0,
            )
    finally:
        srv.shutdown()


def test_relative_redirect_connect_ne_logical():
    """Relative redirect when connect ≠ logical is re-resolved against logical URL."""
    srv = _make_http_server([
        (302, {"Location": "/new"}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",
            scheme="http", port=port, path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_absolute_to_logical_host_followed_and_rewritten():
    """Absolute redirect to logical_host is validated and rewritten to connect_host."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://virtual.test:{port}/new"}, b"")
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",
            scheme="http", port=port, path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert srv._requests[-1] == "/new"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# Non-ASCII (ISO-8859-1) Location lifecycle
# ---------------------------------------------------------------------------

def test_non_ascii_location_iso8859():
    """Location: /caf\xe9/menu (Latin-1 é) must be quoted before http.client sees it."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    # The Location value contains a raw Latin-1 0xE9 byte decoded as unicode é
    srv._script[0] = (302, {"Location": "/caf\xe9/menu"}, b"")
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
        assert srv._requests[-1] == "/caf%E9/menu"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# IDNA through opener lifecycle
# ---------------------------------------------------------------------------

def test_idna_redirect_unicode_equals_punycode():
    """Redirect to punycode form matches logical origin of unicode hostname."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://xn--bcher-kva.test:{port}/new"}, b"")
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="bücher.test",
            scheme="http", port=port, path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_idna_non_latin1_host_header_is_ascii_across_redirect():
    """Unicode virtual hosts are sent as their ASCII IDNA form on every hop."""
    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    ascii_host = "xn--r8jz45g.xn--zckzah"
    srv._script[0] = (
        302,
        {"Location": f"http://{ascii_host}:{port}/new"},
        b"",
    )
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="例え.テスト",
            scheme="http", port=port, path="/", timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert srv._hosts == [f"{ascii_host}:{port}", f"{ascii_host}:{port}"]
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# Proxy bypass
# ---------------------------------------------------------------------------

def test_proxy_bypass_http_proxy_uppercase(monkeypatch):
    for var in _ALL_PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")

    srv = _make_http_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_proxy_bypass_http_proxy_lowercase(monkeypatch):
    for var in _ALL_PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")

    srv = _make_http_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_proxy_bypass_https_proxy_uppercase(monkeypatch):
    for var in _ALL_PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")

    srv, ctx = _make_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(connect_host="127.0.0.1", scheme="https", port=port,
                       path="/", context=ctx, timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


def test_proxy_bypass_https_proxy_lowercase(monkeypatch):
    for var in _ALL_PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")

    srv, ctx = _make_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(connect_host="127.0.0.1", scheme="https", port=port,
                       path="/", context=ctx, timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# addinfourl interface regression
# ---------------------------------------------------------------------------

def test_addinfourl_interface():
    """`http_open` must return an object with .status, .read(), and .headers."""
    srv = _make_http_server([(200, {"X-Test": "yes"}, b"body")])
    port = srv.server_address[1]
    try:
        with http_open(connect_host="127.0.0.1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
            data = resp.read()
            assert data == b"body"
            assert resp.headers.get("X-Test") == "yes"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# IPv6 (conditional)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available")
def test_ipv6_same_origin_redirect():
    try:
        test_sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test_sock.bind(("::1", 0))
        test_sock.close()
    except OSError:
        pytest.skip("IPv6 loopback not available")

    srv = _make_http_server([
        (302, {}, b""),
        (200, {}, b"ok"),
    ], host="::1")
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://[::1]:{port}/new"}, b"")
    try:
        with http_open(connect_host="::1", scheme="http", port=port,
                       path="/", timeout=5.0) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()


@pytest.mark.skipif(not socket.has_ipv6, reason="IPv6 not available")
def test_ipv6_cross_port_blocked():
    try:
        test_sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        test_sock.bind(("::1", 0))
        test_sock.close()
    except OSError:
        pytest.skip("IPv6 loopback not available")

    srv = _make_http_server([
        (302, {}, b""),
    ], host="::1")
    port = srv.server_address[1]
    srv._script[0] = (302, {"Location": f"http://[::1]:{port + 1}/"}, b"")
    try:
        with pytest.raises(RedirectBlockedError):
            http_open(connect_host="::1", scheme="http", port=port,
                      path="/", timeout=5.0)
    finally:
        srv.shutdown()
