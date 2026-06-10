# C2 — Recorded-IP Endpoint Pinning: Implementation Plan (Rev 2)

**Role:** PA
**Card:** C2
**Branch:** development
**Commit:** 834f893
**Status:** clean
**Date:** 2026-06-10

---

## Context

C1 delivered a shared HTTP transport (`shared/http_transport.py`) that correctly pins the
TCP socket destination to `connect_host` (the recorded IP) and sets the `Host` header to
`request_host` (the virtual hostname). However, TLS SNI and certificate identity still
use the URL authority — the IP address — because `urllib`/`http.client` derives
`server_hostname` for TLS directly from `self.host`, which is always the IP in our URL.

Three callers work around this by falling back to `connect_host = hostname`:
- `shared/http_browser.py`: `download_file` and `read_file` use `connect_host=self.request_host or self.ip`
- `gui/utils/http_probe_runner.py` lines 148–167: HTTPS retry with `connect_host=request_host_norm`
- `gui/utils/protocol_extract_runner.py` lines 613–633: same HTTPS retry

These workarounds re-enable the DNS-rebinding risk C2 closes.

---

## Status and Baseline

- Branch `development`, commit `834f893` (C1 accepted), clean.
- 59 tests pass across `test_http_transport.py` and `test_server_list_http_endpoint.py`.

### Touched-file line counts (pre-edit)

| File | Lines |
|------|-------|
| `shared/http_transport.py` | 333 |
| `shared/http_browser.py` | 362 |
| `commands/http/verifier.py` | 243 |
| `gui/utils/http_probe_runner.py` | 325 |
| `gui/utils/protocol_extract_runner.py` | 820 |
| `shared/tests/test_http_transport.py` | 936 |
| `gui/tests/test_http_probe.py` | ~310 |
| `gui/tests/test_protocol_extract_runner.py` | ~320 (estimate) |
| `docs/TECHNICAL_REFERENCE.md` | 1100+ |

All well within the 1700-line stop gate.

---

## Objective

Extend the C1 transport so that:

1. TCP socket connects to `connect_host` (the recorded IP) — already done in C1.
2. `Host` header uses `request_host` — already done in C1.
3. TLS SNI uses `request_host` when present and it is a DNS hostname.
4. TLS certificate identity is verified against `request_host` in strict mode.
5. No hostname fallback exists after an IP connection failure.
6. IPv4 and IPv6 literal `connect_host` values work correctly (no SNI for IP-only).
7. HTTPS calls without a caller-provided context receive pinned treatment — no fall-through to urllib's default handler.

---

## Stop-Gate Assessment: CLEARED

**Requirement:** Confirm a standard-library-only implementation without unsafe global
monkeypatching before code begins.

**RA concern addressed:** `HTTPSConnection.connect()` derives `server_hostname` from
`self.host` in every CPython version 3.8–3.13. Setting `_server_hostname` (as Rev 1
proposed) has no effect — that attribute is not consulted by `connect()`.

**Confirmed correct mechanism:**

`connect()` in every supported Python version:
```
super().connect()                         # HTTPConnection: TCP to self.host (IP)
server_hostname = self._tunnel_host or self.host   # always the IP from URL authority
self._context.wrap_socket(self.sock, server_hostname=server_hostname)
```

The only correct override is to bypass `HTTPSConnection.connect()` entirely and call
`ctx.wrap_socket()` directly with the pinned hostname. This is achieved by calling
`http.client.HTTPConnection.connect(inner_self)` by name (establishing the TCP socket
to the IP) and then calling `ctx.wrap_socket(inner_self.sock, server_hostname=pinned)`.

**Why it is not unsafe global monkeypatching:**
- The `_Conn` class is defined inside `https_open()` and exists only for the life of one
  opener; no global state is mutated.
- `ctx` and `pinned` are captured from the enclosing `https_open` call scope.
- This pattern is standard class inheritance and method override — not patching of any
  existing symbol.

**Python 3.8–3.13 version stability:**
- Python 3.8: `HTTPSConnection.connect()` calls `self._wrap_socket(sock, self.context, sni)` where `_wrap_socket` = `ctx.wrap_socket`. Our override avoids both `self.context` and `_wrap_socket`.
- Python 3.12–3.13: `HTTPSConnection.connect()` calls `self._context.wrap_socket(sock, server_hostname=sni)`. Our override avoids `self._context`.
- We use `ctx` from the closure and call `ctx.wrap_socket()` directly — immune to the `self.context` → `self._context` rename.

**Stop gate: CLEARED. Proceed.**

---

## Non-Goals

- No new external dependencies.
- No database schema or migration changes.
- No auth or Web UI changes.
- No changes to SMB, FTP, or non-HTTP paths.
- No strict-TLS-by-default change (default remains `allow_insecure_tls=True`).
- No GUI-to-workflow boundary changes.
- No changes to `commands/http/verifier.py` — it already passes `connect_host=ip` and
  `request_host` correctly to `http_open`, and always creates an explicit context for HTTPS.

---

## Confirmed Root Cause

`http_open()` builds `url` with `connect_host` (the IP) as URL authority. The resulting
`http.client.HTTPSConnection` sets `self.host` to the IP and `connect()` computes:
```python
server_hostname = self._tunnel_host or self.host  # = IP
self._context.wrap_socket(self.sock, server_hostname=server_hostname)
```

TLS handshake sends IP as SNI. Server cert is validated against the IP, not the virtual
hostname. Strict TLS fails (cert is for hostname). Insecure TLS silently misdirects SNI,
breaking virtual hosting.

---

## Exact Behavior and Interfaces

### `_PinnedHTTPSHandler` (new, internal to `http_transport.py`)

```python
import http.client  # add to existing stdlib imports

class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """
    HTTPS handler that pins TLS SNI and certificate identity to server_hostname
    while connecting the TCP socket to the IP in the URL authority (connect_host).

    HTTPSConnection.connect() in every CPython 3.8–3.13 derives server_hostname
    from self.host (the URL authority = IP).  We must bypass it entirely.

    Implementation:
      1. HTTPConnection.connect(self) — called by name (grandparent) to establish
         the plain TCP socket to self.host (the recorded IP).
      2. ctx.wrap_socket(sock, server_hostname=pinned) — wraps directly using the
         virtual hostname; avoids the self.context / self._context name split across
         Python versions.

    ProxyHandler({}) disables all tunneling, so _tunnel_host is always None here.
    """

    def __init__(self, context: ssl.SSLContext, server_hostname: str) -> None:
        super().__init__(context=context)
        self._pinned_hostname = server_hostname

    def https_open(self, req) -> object:
        pinned = self._pinned_hostname
        ctx = self._context  # set by super().__init__(context=...)

        class _Conn(http.client.HTTPSConnection):
            def connect(inner_self) -> None:
                # Step 1: establish TCP socket to self.host (the IP).
                http.client.HTTPConnection.connect(inner_self)
                # Step 2: wrap with TLS; server_hostname drives SNI + cert identity.
                inner_self.sock = ctx.wrap_socket(
                    inner_self.sock, server_hostname=pinned
                )

        return self.do_open(_Conn, req, context=ctx)
```

### `http_open` — two changes

**Change A: always materialise a context for HTTPS.**

When `scheme == "https"` and `context is None`, create `ssl.create_default_context()`.
This prevents urllib's built-in default `HTTPSHandler` (added by `build_opener` as a
default class) from being used, which would derive SNI from the IP.

```python
effective_ctx = context
if scheme.lower() == "https" and effective_ctx is None:
    effective_ctx = ssl.create_default_context()
```

Pass `effective_ctx` everywhere `context` / `ctx` is used downstream.

**Change B: derive `server_hostname` for pinning.**

SNI is only valid for DNS hostnames; IP literals are skipped per RFC 6066.

```python
server_hostname: Optional[str] = None
if scheme_lower == "https" and request_host:
    try:
        ipaddress.ip_address(logical_host_norm.strip("[]"))
    except ValueError:
        server_hostname = logical_host_norm  # DNS hostname → pin as SNI
```

Pass `server_hostname=server_hostname` to `_make_opener`.

### `_make_opener` — updated signature

```python
def _make_opener(
    ctx: Optional[ssl.SSLContext],
    redirect_handler: "urllib.request.BaseHandler",
    server_hostname: Optional[str] = None,   # NEW
) -> urllib.request.OpenerDirector:
    handlers = [
        urllib.request.ProxyHandler({}),
        redirect_handler,
    ]
    if ctx is not None:
        if server_hostname:
            handlers.append(_PinnedHTTPSHandler(ctx, server_hostname))
        else:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)
```

Because `http_open` now guarantees `effective_ctx is not None` for any HTTPS call, the
`if ctx is not None` branch always fires for HTTPS. `_PinnedHTTPSHandler` (a subclass of
`HTTPSHandler`) causes `build_opener` to skip the default `HTTPSHandler` via its skip
mechanism, ensuring exactly one HTTPS handler is active per opener.

### `http_open` public signature: unchanged

No caller API changes needed.

---

## File-by-File Changes

### 1. `shared/http_transport.py` (+30–35 lines)

a. Add `import http.client` at top (new stdlib import).

b. Add `_PinnedHTTPSHandler` class after `_make_opener` (~25 lines, per spec above).

c. Update `_make_opener` signature and body (3–4 line delta, per spec above).

d. Update `http_open`:
   - Compute `effective_ctx` (3 lines after `scheme_lower` assignment).
   - Compute `server_hostname` (5 lines, using `logical_host_norm` already in scope).
   - Pass `effective_ctx` to `_make_opener` and `server_hostname=server_hostname`.

### 2. `shared/http_browser.py` (3 one-line fixes)

`HttpNavigator` already stores `self.ip` and `self.request_host` correctly. Fix three
call sites that ignore `self.ip`:

| Method | Current (buggy) | Fixed |
|--------|-----------------|-------|
| `list_dir` (~line 180) | `try_http_request(self.request_host or self.ip, ...)` | `try_http_request(self.ip, ...)` |
| `download_file` (~line 254) | `connect_host=self.request_host or self.ip` | `connect_host=self.ip` |
| `read_file` (~line 335) | `connect_host=self.request_host or self.ip` | `connect_host=self.ip` |

### 3. `gui/utils/http_probe_runner.py` (~−20 lines)

**Remove** the entire HTTPS fallback block (lines 148–167):

```python
# This block re-enables DNS rebinding by using the hostname as socket destination.
# C2 removes it: the transport now pins SNI correctly via _PinnedHTTPSHandler.
if (
    scheme_norm == "https"
    and request_host_norm
    and request_host_norm != ip
):
    ...
    active_connect_host = request_host_norm   # ← removed
    ...
```

After removal, `active_connect_host` is set only at lines 100 and 143 (both to `ip`).
The directory traversal loop at line 210 continues to use `active_connect_host` —
it now always equals `ip`. No other changes to the traversal logic.

### 4. `gui/utils/protocol_extract_runner.py` (~−20 lines)

**Remove** the HTTPS fallback block (lines 613–633) that sets
`active_connect_host = active_request_host`. After removal, `active_connect_host` stays
`ip_address` throughout (lines 582, 609). Lines 671 and 734 are unchanged.

### 5. `shared/tests/test_http_transport.py` (+70–90 lines, 4 new tests)

**New helper: `_make_sni_recording_https_server`**

Builds an HTTPS server with an SNI recording callback. On each TLS handshake, appends
`server_name` (or `None`) to `server._sni_log`. Uses the existing self-signed cert for
"localhost". Returns `(server, insecure_client_ctx)`.

```python
def _make_sni_recording_https_server(script):
    cert_pem, key_pem = _make_self_signed_cert()
    server = http.server.HTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server._script = script; server._script_idx = 0
    server._requests = []; server._hosts = []
    server._sni_log = []  # NEW: captured SNI values

    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # write cert/key to tmp files, load, unlink (same as existing _make_https_server)
    ...
    def _sni_cb(ssl_socket, server_name, ssl_ctx):
        server._sni_log.append(server_name)
        return None
    srv_ctx.set_servername_callback(_sni_cb)
    server.socket = srv_ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    insecure_ctx = ssl.create_default_context()
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE
    return server, insecure_ctx
```

**New helper: `_make_trusted_https_server`**

Builds HTTPS server (same cert) plus a STRICT client context that trusts that cert.
Returns `(server, strict_client_ctx)`.

```python
def _make_trusted_https_server(script):
    cert_pem, key_pem = _make_self_signed_cert()
    # ... build server (same as _make_https_server) ...
    # Build strict client context
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(cert_pem); cert_path = f.name
    strict_ctx = ssl.create_default_context()
    strict_ctx.load_verify_locations(cert_path)
    os.unlink(cert_path)
    # defaults: check_hostname=True, verify_mode=CERT_REQUIRED
    return server, strict_ctx
```

**Test 1: `test_sni_transmitted_equals_request_host`** — direct SNI capture

```python
def test_sni_transmitted_equals_request_host():
    """SNI value transmitted during TLS handshake equals request_host, not the IP."""
    srv, insecure_ctx = _make_sni_recording_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="localhost",
            scheme="https", port=port, path="/",
            context=insecure_ctx, timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert srv._sni_log == ["localhost"]   # proves SNI = virtual hostname
    finally:
        srv.shutdown()
```

**Test 2: `test_pinned_https_request_host_never_dns_resolved`** — DNS proof via `getaddrinfo` spy + HTTPS

Monkeypatch `socket.getaddrinfo` to record every hostname passed to it, then make an
HTTPS request through `_PinnedHTTPSHandler` with a non-resolvable `request_host`.
Two assertions: the request succeeds (socket went to the IP), and `request_host` never
appeared in the `getaddrinfo` call list (DNS was not consulted for it). SNI is
confirmed via `srv._sni_log`.

```python
def test_pinned_https_request_host_never_dns_resolved(monkeypatch):
    """socket.getaddrinfo must not be called with request_host.
    Only connect_host (IP) must reach socket creation.
    Uses _PinnedHTTPSHandler path (HTTPS + request_host hostname)."""
    resolved = []
    real_getaddrinfo = socket.getaddrinfo

    def _spy(host, port, *args, **kwargs):
        resolved.append(host)
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _spy)

    srv, insecure_ctx = _make_sni_recording_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",    # non-resolvable; DNS lookup would raise
            scheme="https", port=port, path="/",
            context=insecure_ctx, timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert "virtual.test" not in resolved, (
            f"request_host was DNS-resolved: {resolved}"
        )
        assert "virtual.test" in srv._sni_log   # sent as SNI, not socket destination
    finally:
        srv.shutdown()
```

**Test 3: `test_pinned_sni_strict_tls_succeeds`** — strict TLS round-trip

```python
def test_pinned_sni_strict_tls_succeeds():
    """Strict TLS succeeds when SNI is pinned to the hostname matching the cert.
    Without C2, SNI would be '127.0.0.1' and cert check for 'localhost' would fail."""
    srv, strict_ctx = _make_trusted_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="localhost",
            scheme="https", port=port, path="/",
            context=strict_ctx, timeout=5.0,
        ) as resp:
            assert resp.status == 200
    finally:
        srv.shutdown()
```

**Test 4a: `test_ip_only_no_sni_transmitted`** — direct assertion that SNI is suppressed for IP literals

Python's ssl module follows RFC 6066: SNI must not be sent for IP literals.
`wrap_socket(server_hostname="127.0.0.1")` causes OpenSSL to suppress the SNI
extension; the servername callback receives `None`.

```python
def test_ip_only_no_sni_transmitted():
    """Python suppresses SNI for IP literals (RFC 6066).
    Without request_host the servername callback receives None, not '127.0.0.1'."""
    srv, insecure_ctx = _make_sni_recording_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with http_open(
            connect_host="127.0.0.1",
            scheme="https", port=port, path="/",
            context=insecure_ctx, timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert srv._sni_log == [None]
    finally:
        srv.shutdown()
```

**Test 4b: `test_ip_only_no_request_host_strict_tls_fails`** — no spurious pinning

Cert validation uses the socket hostname ("127.0.0.1") as expected identity.
Cert is for "localhost" → strict TLS fails. SNI is suppressed (as in 4a);
the failure is a hostname-mismatch in certificate verification, not SNI.

```python
def test_ip_only_no_request_host_strict_tls_fails():
    """Strict TLS fails for IP-only: cert is for 'localhost', identity check uses
    '127.0.0.1'. SNI is suppressed for IP literals — failure is cert mismatch."""
    srv, strict_ctx = _make_trusted_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    try:
        with pytest.raises(urllib.error.URLError):
            http_open(
                connect_host="127.0.0.1",
                scheme="https", port=port, path="/",
                context=strict_ctx, timeout=5.0,
            )
    finally:
        srv.shutdown()
```

**Test 5: `test_default_context_pins_sni_without_explicit_context`** — context=None path

When `context=None` is passed for HTTPS, `http_open` must create a default context and
still route through `_PinnedHTTPSHandler`, not fall back to urllib's built-in default
handler (which would use the IP as `server_hostname`). Monkeypatch
`ssl.create_default_context` to return the insecure context so the self-signed cert is
accepted; this isolates the test to the SNI-pinning path.

```python
def test_default_context_pins_sni_without_explicit_context(monkeypatch):
    """context=None triggers the default-context branch; SNI is still pinned to
    request_host and request_host is never passed to socket.getaddrinfo."""
    resolved = []
    real_getaddrinfo = socket.getaddrinfo

    def _spy(host, port, *args, **kwargs):
        resolved.append(host)
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _spy)

    srv, insecure_ctx = _make_sni_recording_https_server([(200, {}, b"ok")])
    port = srv.server_address[1]
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **kw: insecure_ctx)

    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",
            scheme="https", port=port, path="/",
            # No context= argument — exercises the default-context branch
            timeout=5.0,
        ) as resp:
            assert resp.status == 200
        assert "virtual.test" not in resolved
        assert "virtual.test" in srv._sni_log
    finally:
        srv.shutdown()
```

**Test 6: `test_https_redirect_preserves_pinned_ip_and_sni`** — every redirect hop

A same-origin HTTPS redirect rewrites the Location URL to the IP authority (C1).
The second hop must also use `_PinnedHTTPSHandler` (same opener instance), creating a
new TLS connection with `server_hostname=pinned`. Both TLS handshakes appear in
`_sni_log`.

```python
def test_https_redirect_preserves_pinned_ip_and_sni():
    """Every redirect hop retains the recorded-IP socket destination and hostname SNI."""
    srv, insecure_ctx = _make_sni_recording_https_server([
        (302, {}, b""),    # Location patched below after port is known
        (200, {}, b"ok"),
    ])
    port = srv.server_address[1]
    # Same-origin redirect via virtual hostname — handler rewrites to IP authority
    srv._script[0] = (302, {"Location": f"https://virtual.test:{port}/new"}, b"")
    try:
        with http_open(
            connect_host="127.0.0.1",
            request_host="virtual.test",
            scheme="https", port=port, path="/",
            context=insecure_ctx, timeout=5.0,
        ) as resp:
            assert resp.status == 200
        # Both requests reached the IP-bound server
        assert srv._requests == ["/", "/new"]
        # Both TLS handshakes used the hostname as SNI
        assert srv._sni_log == ["virtual.test", "virtual.test"]
    finally:
        srv.shutdown()
```

### 6. `gui/tests/test_http_probe.py` — replace 1 test

**Delete** `test_https_retry_uses_request_host_authority_when_ip_attempt_fails`
(line 282–309); it asserts `request_calls[1][0] == "www.bound2burst.net"` which is the
hostname-fallback behavior C2 removes.

**Replace with** `test_https_ip_only_no_hostname_retry_on_failure`:

```python
def test_https_ip_only_no_hostname_retry_on_failure(tmp_path, monkeypatch):
    """C2: when IP-based HTTPS attempt fails, transport must not retry via hostname."""
    monkeypatch.setattr("gui.utils.http_probe_cache.HTTP_CACHE_DIR", tmp_path)

    request_calls = []

    def _fake_request(ip, port, scheme, allow_insecure_tls, timeout,
                      path="/", request_host=None):
        request_calls.append((ip, path, request_host))
        return 0, b"", False, "connect_fail"

    with patch("gui.utils.http_probe_runner.try_http_request",
               side_effect=_fake_request), \
         patch("gui.utils.http_probe_runner.validate_index_page",
               return_value=False), \
         patch("gui.utils.http_probe_runner._parse_dir_entries",
               return_value=([], [])):
        snapshot = run_http_probe(
            "67.205.33.18",
            port=443,
            scheme="https",
            request_host="www.bound2burst.net",
            start_path="/movies/",
        )

    # All calls must use the recorded IP as connect_host, never the hostname.
    assert all(call[0] == "67.205.33.18" for call in request_calls), (
        f"Hostname used as socket destination: {request_calls}"
    )
    assert len(snapshot["errors"]) > 0   # failure propagated, not silently swallowed
```

### 7. `docs/TECHNICAL_REFERENCE.md` — 1 paragraph update (lines 1108–1112)

The current text states "requests use that hostname as the URL authority (including HTTPS
SNI)", which is incorrect after C2. Replace lines 1108–1112 with:

```
The HTTP browser keeps the server IP as its database, cache, and quarantine identity.
When `http_servers.probe_host` is present, the recorded IP is the TCP socket
destination; the saved hostname is used only for the HTTP `Host` header, TLS SNI,
and certificate identity in strict mode. Startup navigation uses
`http_servers.probe_path`. This matches Server List `Copy URL` behavior and supports
virtual-hosted directory indexes.
```

Key change: "requests use that hostname as the URL authority (including HTTPS SNI)"
→ "the recorded IP is the TCP socket destination; the saved hostname is used only for
the HTTP Host header, TLS SNI, and certificate identity in strict mode."

### 9. `gui/tests/test_protocol_extract_runner.py` — add 1 test

Add `test_https_extract_no_hostname_retry_on_ip_failure` after existing HTTP tests:

```python
def test_https_extract_no_hostname_retry_on_ip_failure(monkeypatch, tmp_path):
    """C2: HTTPS extract must not retry with connect_host=hostname when IP fails."""
    fetch_calls = []

    def _fake_fetch(**kwargs):
        fetch_calls.append(kwargs.get("connect_host"))
        return False, [], [], "connect_fail"

    monkeypatch.setattr(per, "_http_fetch_listing", _fake_fetch)
    monkeypatch.setattr(per, "_http_download_file", lambda **k: None)
    monkeypatch.setattr(per, "log_quarantine_event", lambda *a, **k: None)

    per.run_http_extract(
        "203.0.113.5",
        port=443,
        scheme="https",
        request_host="cdn.example.org",
        start_path="/data/",
        allow_insecure_tls=True,
        download_dir=tmp_path / "out",
        max_total_bytes=10_000,
        max_file_bytes=10_000,
        max_file_count=10,
        max_seconds=60,
        max_depth=1,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        extension_mode="allow_all",
        clamav_config={"enabled": False},
    )

    # All _http_fetch_listing calls must use the IP, never the hostname.
    assert all(h == "203.0.113.5" for h in fetch_calls), (
        f"Hostname used as socket destination: {fetch_calls}"
    )
```

---

## Edge and Failure Cases

| Case | Behavior |
|------|----------|
| `request_host` is an IPv4 literal | `logical_host_norm` is IP; IP check fires; `server_hostname = None`; plain `HTTPSHandler` used |
| `request_host` is an IPv6 literal | Same; no SNI |
| `connect_host` is IPv6, `request_host` is hostname | TCP to IPv6 IP; SNI = hostname; cert validated against hostname |
| `request_host` is None | `server_hostname = None`; `HTTPSHandler` used; Python suppresses SNI for IP literals (callback receives `None`); cert identity check still uses the IP |
| `scheme = "http"` with `request_host` | `server_hostname = None`; no HTTPS handler; no change from C1 |
| `context = None`, `scheme = "https"` | `effective_ctx = ssl.create_default_context()`; pinning applied if `request_host` is hostname |
| HTTPS fallback removed (probe/extract) | IP attempt must succeed; if it fails, error is returned — no retry via hostname |
| Strict TLS + IP-only, cert for hostname | Strict TLS fails `ssl.SSLError` → `URLError`; correct and unchanged |
| Redirect on pinned HTTPS | Redirect URL is rewritten to `connect_host` (IP) by C1 handler; `_PinnedHTTPSHandler` applies per-opener, covering all hops |

---

## Tests and Exact Commands

### Focused regression (must all pass)

```bash
./venv/bin/python -m pytest shared/tests/test_http_transport.py -q
./venv/bin/python -m pytest \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_protocol_extract_runner.py \
  gui/tests/test_server_list_http_endpoint.py -q
```

### Negative structural checks (must produce no output)

```bash
# No hostname-or-IP ambiguity in connect_host assignment
rg -n "connect_host=.*request_host or\|connect_host=.*or self\.ip" \
  shared/http_browser.py gui/utils/http_probe_runner.py \
  gui/utils/protocol_extract_runner.py

# No hostname fallback remains in probe/extract
rg -n "active_connect_host = .*request_host\|active_connect_host = active_request_host" \
  gui/utils/http_probe_runner.py gui/utils/protocol_extract_runner.py
```

### Documentation drift check

```bash
grep -n "URL authority\|hostname.*authority\|as the URL authority" \
  docs/TECHNICAL_REFERENCE.md
# Expected: line 1108 should no longer say "use that hostname as the URL authority"
```

### Full baseline

```bash
./venv/bin/python -m pytest shared/tests/ gui/tests/ \
  experimental/webui/tests/ -q
```

---

## Line-Count Risk

| File | Pre | Est. Post | Risk |
|------|-----|-----------|------|
| `shared/http_transport.py` | 333 | ~365 | None |
| `shared/http_browser.py` | 362 | 362 | None (1-char changes) |
| `gui/utils/http_probe_runner.py` | 325 | ~305 | None (net removal) |
| `gui/utils/protocol_extract_runner.py` | 820 | ~800 | None (net removal) |
| `shared/tests/test_http_transport.py` | 936 | ~1100 | None |
| `gui/tests/test_http_probe.py` | ~310 | ~320 | None (1 test replaced) |
| `gui/tests/test_protocol_extract_runner.py` | ~320 | ~360 | None |
| `docs/TECHNICAL_REFERENCE.md` | 1100+ | +0 net (5-line rewrite) | None |

No file approaches 1700 lines.

---

## Rollback

1. `git diff` to review.
2. `git checkout -- <file>` per file if needed.
3. Re-run: `./venv/bin/python -m pytest shared/tests/test_http_transport.py -q`.

No schema, migration, config, or auth changes to reverse.

---

## DA Handoff Prompt

```text
You are Claude working on Dirracuda under HI/RA supervision.

Repo: /home/kevin/DEV/dirracuda
Branch: development
Commit: 834f893
Role: DA
Card: C2 — Recorded-IP Endpoint Pinning
Approved plan: docs/dev/security_review_10JUN26/approved_plans/C2_endpoint_pinning.md

Read and follow:
- AGENTS.md, README.md, CLAUDE.md, docs/TECHNICAL_REFERENCE.md
- docs/dev/security_review_10JUN26/CLAUDE_PROMPTS.md
- docs/dev/security_review_10JUN26/approved_plans/C1_http_redirect_policy.md
  (predecessor transport; do not re-edit)

Implement exactly the approved plan. No new deps, no schema changes, no auth changes.

Critical implementation note: _PinnedHTTPSHandler._Conn.connect() must call
http.client.HTTPConnection.connect(inner_self) by name (grandparent), then call
ctx.wrap_socket(inner_self.sock, server_hostname=pinned) directly.
Do NOT call super().connect() from _Conn — that would invoke
HTTPSConnection.connect() which derives server_hostname from self.host (the IP).

Before editing:
- Confirm branch, commit, clean status, and touched-file line counts.
- Run: ./venv/bin/python -m pytest shared/tests/test_http_transport.py -q

After editing:
- Run the exact validation commands in the plan.
- Review README.md and docs/TECHNICAL_REFERENCE.md for drift.
- Do not commit. Report using the DA required response format.
```
