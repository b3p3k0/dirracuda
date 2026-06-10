"""
Shared redirect-safe HTTP transport for all target HTTP operations (C1).

Public surface:
  http_open(...)  — single entry point; returns addinfourl context manager.
  RedirectBlockedError — URLError subclass; raised for any blocked redirect.
  RedirectLimitError   — URLError subclass; raised when MAX_REDIRECTS exceeded.

Design invariants (ARCHITECTURE.md redirect algorithm):
  1. URL authority is always connect_host (IP or hostname, caller decides).
  2. Redirect identity is validated against logical_origin (request_host or
     connect_host), not the physical URL authority.
  3. Relative redirects are re-resolved against the logical URL so that IP-URL
     callers (verifier, extract) compare against the logical hostname.
  4. After validation, the new request URL is rewritten to use connect_host so
     the socket destination is preserved (no DNS re-resolution).
  5. Proxy environment variables are suppressed via ProxyHandler({}).
"""
from __future__ import annotations

import http.client
import ipaddress
import ssl
import string
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------

class RedirectBlockedError(urllib.error.URLError):
    """Cross-origin, credential-bearing, malformed, or locationless redirect."""


class RedirectLimitError(urllib.error.URLError):
    """More than MAX_REDIRECTS same-origin hops attempted."""


# ---------------------------------------------------------------------------
# Internal constants and helpers
# ---------------------------------------------------------------------------

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _bracket_ipv6(host: str) -> str:
    """Return [addr] if host is an IPv6 literal; host unchanged for hostnames."""
    stripped = host.strip("[]")
    try:
        ip = ipaddress.ip_address(stripped)
        return f"[{ip.compressed}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
    except ValueError:
        return host


def _normalize_hostname(h: str) -> str:
    """
    Return canonical comparison form (unbracketed IP or IDNA hostname).
    Always raises ValueError on any invalid input — never propagates UnicodeError.
    Attacker-controlled hostnames with oversized or invalid IDNA labels raise
    UnicodeEncodeError from str.encode('idna'); wrapping as ValueError ensures
    callers' except-ValueError blocks provide deterministic cleanup.
    """
    if not h:
        raise ValueError("empty hostname")
    stripped = h.strip("[]")
    try:
        return ipaddress.ip_address(stripped).compressed.lower()
    except ValueError:
        pass
    try:
        return stripped.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(f"invalid IDNA hostname: {exc}") from exc


def _host_header_value(host: str, port: int, scheme: str) -> str:
    """Format host:port for the HTTP Host header per RFC 7230."""
    default_port = _DEFAULT_PORTS.get(scheme.lower(), 80)
    bracketed = _bracket_ipv6(_normalize_hostname(host))
    return bracketed if port == default_port else f"{bracketed}:{port}"


def _close_fp(fp: object) -> None:
    """Close a redirect response object to free the file descriptor."""
    if fp is None:
        return
    try:
        fp.close()  # type: ignore[union-attr]
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Proxy-disabled opener factory
# ---------------------------------------------------------------------------

def _make_opener(
    ctx: Optional[ssl.SSLContext],
    redirect_handler: "urllib.request.BaseHandler",
    server_hostname: Optional[str] = None,
) -> urllib.request.OpenerDirector:
    handlers = [
        urllib.request.ProxyHandler({}),  # {} disables all env-driven proxies
        redirect_handler,
    ]
    if ctx is not None:
        if server_hostname:
            handlers.append(_PinnedHTTPSHandler(ctx, server_hostname))
        else:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


# ---------------------------------------------------------------------------
# Pinned-SNI HTTPS handler (C2)
# ---------------------------------------------------------------------------

class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """
    HTTPS handler that pins TLS SNI and certificate identity to server_hostname
    while connecting the TCP socket to the IP in the URL authority (connect_host).

    HTTPSConnection.connect() in every CPython 3.8–3.13 derives server_hostname
    from self.host (the URL authority = IP).  We bypass it entirely:
      1. http.client.HTTPConnection.connect() — called by name (grandparent) to
         establish the plain TCP socket to self.host (the recorded IP).
      2. ctx.wrap_socket(sock, server_hostname=pinned) — wraps directly using the
         virtual hostname; avoids the self.context / self._context name split across
         Python versions (3.8 uses self.context; 3.12+ uses self._context).

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
                # Step 1: establish TCP socket to self.host (the recorded IP).
                http.client.HTTPConnection.connect(inner_self)
                # Step 2: wrap with TLS; server_hostname drives SNI + cert identity.
                inner_self.sock = ctx.wrap_socket(
                    inner_self.sock, server_hostname=pinned
                )

        return self.do_open(_Conn, req, context=ctx)


# ---------------------------------------------------------------------------
# Same-origin redirect handler
# ---------------------------------------------------------------------------

class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    MAX_REDIRECTS = 3

    def __init__(
        self,
        connect_host_raw: str,
        connect_host_norm: str,
        logical_host_raw: str,
        logical_host_norm: str,
        logical_origin: tuple,
    ) -> None:
        super().__init__()
        self._connect_host_raw  = connect_host_raw   # for URL rewrite (step 8)
        self._connect_host_norm = connect_host_norm  # for relative-redirect detection
        self._logical_host_raw  = logical_host_raw   # for logical-URL reconstruction
        self._logical_host_norm = logical_host_norm
        self._logical_origin    = logical_origin     # (scheme, norm_host, port)
        self._port              = logical_origin[2]
        self._hops              = 0
        # Per-hop state set synchronously in _handle_redirect before redirect_request
        self._redirect_is_relative  = False
        self._redirect_raw_location = ""

    # --- Single implementation for all 3xx codes (Python 3.8 compatible) -----
    #
    # Does NOT call super().http_error_NNN() to avoid:
    #   - AttributeError on Python <3.11 (http_error_308 not defined there)
    #   - fp leak when exceptions bypass parent's fp.read()/fp.close() cleanup

    def _handle_redirect(self, req, fp, code, msg, headers):
        loc = headers.get("location") or headers.get("uri") or ""
        stripped = loc.strip()
        if not stripped:
            _close_fp(fp)
            raise RedirectBlockedError("redirect_blocked")

        # Replicate urllib's ISO-8859-1 quoting step (CPython http_error_302).
        # http.client.parse_headers() decodes header bytes as ISO-8859-1, so
        # the Location value may contain Latin-1 characters (e.g. é = 0xE9).
        # Percent-encode non-ASCII bytes before urljoin/http.client sees them,
        # or a valid same-origin redirect with a non-ASCII path would later fail
        # with UnicodeEncodeError inside http.client.  Space is also encoded
        # here.
        try:
            quoted = urllib.parse.quote(
                stripped, encoding="iso-8859-1", safe=string.punctuation)
        except (UnicodeEncodeError, UnicodeDecodeError):
            _close_fp(fp)
            raise RedirectBlockedError("redirect_blocked")

        # urlparse and urljoin raise ValueError for malformed authority syntax
        # (e.g. [:::1] invalid IPv6).  Catch here so fp is closed and the caller
        # receives the deterministic RedirectBlockedError, not a raw ValueError.
        try:
            parsed_loc = urllib.parse.urlparse(quoted)
            self._redirect_is_relative  = not parsed_loc.scheme and not parsed_loc.netloc
            self._redirect_raw_location = quoted   # quoted form used for re-resolution
            newurl = urllib.parse.urljoin(req.full_url, quoted)
        except ValueError:
            _close_fp(fp)
            raise RedirectBlockedError("redirect_blocked")

        try:
            new_req = self.redirect_request(req, fp, code, msg, headers, newurl)
        except (RedirectBlockedError, RedirectLimitError):
            _close_fp(fp)
            raise

        # Drain and close before opening the next hop (matches parent behaviour;
        # allows keep-alive on non-blocking connections).
        try:
            fp.read()
        except Exception:
            pass
        _close_fp(fp)

        return self.parent.open(new_req, timeout=req.timeout)

    http_error_301 = _handle_redirect
    http_error_302 = _handle_redirect
    http_error_303 = _handle_redirect
    http_error_307 = _handle_redirect
    http_error_308 = _handle_redirect

    # --- Per-hop validation and rewrite ---------------------------------------

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Reject self-referential
        if newurl == req.full_url:
            raise RedirectBlockedError("redirect_blocked")

        self._hops += 1
        if self._hops > self.MAX_REDIRECTS:
            raise RedirectLimitError("redirect_limit")

        # ARCHITECTURE step 3: resolve against the logical URL.
        # _handle_redirect called urljoin against the physical URL (connect_host).
        # For relative redirects when connect != logical, re-resolve the raw
        # Location against the logical URL so the target carries logical_host
        # authority and comparison against logical_origin is correct.
        if (self._redirect_is_relative
                and self._connect_host_norm != self._logical_host_norm):
            phys = urllib.parse.urlparse(req.full_url)
            logical_base = urllib.parse.urlunparse((
                phys.scheme,
                f"{_bracket_ipv6(self._logical_host_raw)}:{self._port}",
                phys.path, phys.params, phys.query, ''
            ))
            url_for_check = urllib.parse.urljoin(
                logical_base, self._redirect_raw_location
            )
        else:
            url_for_check = newurl

        # Parse
        try:
            new = urllib.parse.urlparse(url_for_check)
            new_scheme        = (new.scheme or "").lower()
            new_host_raw      = new.hostname or ""
            new_port_explicit = new.port   # raises ValueError on non-integer port
        except ValueError:
            raise RedirectBlockedError("redirect_blocked")

        # ARCHITECTURE step 6: reject userinfo, unsupported schemes, bad ports
        if new.username or new.password:
            raise RedirectBlockedError("redirect_blocked")
        if new_scheme not in ("http", "https") or not new_host_raw:
            raise RedirectBlockedError("redirect_blocked")
        if new_port_explicit is not None and not (1 <= new_port_explicit <= 65535):
            raise RedirectBlockedError("redirect_blocked")

        new_eff_port = (new_port_explicit if new_port_explicit is not None
                        else _DEFAULT_PORTS.get(new_scheme, 80))

        try:
            new_host_norm = _normalize_hostname(new_host_raw)
        except ValueError:
            raise RedirectBlockedError("redirect_blocked")

        # ARCHITECTURE step 5: reject if any identity field changes.
        # Logical origin only — no dual-origin check.
        orig_scheme, orig_host_norm, orig_port = self._logical_origin
        if (new_scheme     != orig_scheme
                or new_host_norm != orig_host_norm
                or new_eff_port  != orig_port):
            raise RedirectBlockedError("redirect_blocked")

        # ARCHITECTURE step 8: preserve socket destination; update path, params,
        # and query only.  Include new.params to preserve RFC 3986 path parameters
        # (e.g. /path;token?x=1 must not become /path?x=1).
        new_path = new.path
        if new.params:
            new_path += ";" + new.params
        if new.query:
            new_path += "?" + new.query
        rewritten = (f"{orig_scheme}://"
                     f"{_bracket_ipv6(self._connect_host_raw)}:{orig_port}"
                     f"{new_path}")

        # Normalize code=308 to 307 for Python <3.11 compatibility.
        # Both codes have identical method-preservation semantics for GET/HEAD.
        compat_code = 307 if code == 308 else code
        return super().redirect_request(req, fp, compat_code, msg, headers, rewritten)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def http_open(
    *,
    connect_host: str,
    request_host: Optional[str] = None,
    scheme: str,
    port: int,
    path: str = "/",
    headers: Optional[dict] = None,
    context: Optional[ssl.SSLContext] = None,
    timeout: float,
):
    """
    Open one HTTP request through a proxy-disabled, same-origin-redirect-enforcing
    opener.  Returns an addinfourl context manager (drop-in for urlopen return value).

    connect_host  — URL authority and socket destination (IP or hostname).
    request_host  — virtual-host name for Host header and origin identity only.
                    If None, connect_host is used for both roles.
    """
    logical_raw = request_host or connect_host
    try:
        connect_host_norm = _normalize_hostname(connect_host)
        logical_host_norm = _normalize_hostname(logical_raw)
    except ValueError as exc:
        raise urllib.error.URLError(f"invalid host: {exc}") from exc

    clean_path = "/" + (path or "/").lstrip("/")
    url = f"{scheme}://{_bracket_ipv6(connect_host_norm)}:{port}{clean_path}"

    req_headers = dict(headers or {})
    req_headers["Host"] = _host_header_value(
        logical_host_norm, port, scheme
    )

    scheme_lower = scheme.lower()
    logical_origin = (scheme_lower, logical_host_norm, port)

    # C2: ensure a context is always present for HTTPS so urllib's built-in default
    # HTTPSHandler (which derives SNI from the IP URL authority) is never used.
    effective_ctx = context
    if scheme_lower == "https" and effective_ctx is None:
        effective_ctx = ssl.create_default_context()

    # C2: derive server_hostname for SNI pinning.
    # RFC 6066: SNI must not be sent for IP literals; skip if logical host is an IP.
    server_hostname: Optional[str] = None
    if scheme_lower == "https" and request_host:
        try:
            ipaddress.ip_address(logical_host_norm.strip("[]"))
        except ValueError:
            server_hostname = logical_host_norm  # DNS hostname — pin as SNI

    redirect_handler = _SameOriginRedirectHandler(
        connect_host_raw=connect_host_norm,
        connect_host_norm=connect_host_norm,
        logical_host_raw=logical_host_norm,
        logical_host_norm=logical_host_norm,
        logical_origin=logical_origin,
    )
    opener = _make_opener(effective_ctx, redirect_handler, server_hostname=server_hostname)
    req = urllib.request.Request(url, headers=req_headers)
    return opener.open(req, timeout=timeout)
