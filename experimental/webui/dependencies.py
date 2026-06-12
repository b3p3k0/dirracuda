"""FastAPI auth dependency and CSRF/origin validation helpers."""

import hmac
import urllib.parse
from typing import Optional

from fastapi import Request

from experimental.webui.sessions import Session, cookie_name


class AuthRequired(Exception):
    """Raised by get_session when no valid session exists."""


def get_session(request: Request) -> Session:
    """Return the current session or raise AuthRequired."""
    cfg = request.app.state.cfg
    store = request.app.state.session_store
    sid = request.cookies.get(cookie_name(cfg.tls.enabled))
    if not sid:
        raise AuthRequired()
    s = store.get(sid, cfg.session_timeout_idle, cfg.session_timeout_absolute)
    if s is None:
        raise AuthRequired()
    return s


def validate_csrf(request_token: Optional[str], session_csrf: str) -> bool:
    if not request_token:
        return False
    return hmac.compare_digest(request_token, session_csrf)


def same_origin(request: Request) -> bool:
    """Return False if a cross-origin Origin or Referer header is detected.

    For Origin: compares scheme and netloc exactly.
    For Referer: compares netloc only -- intentional for localhost/dev where
    the scheme may differ at the referer level.
    Absent headers pass; do not penalize requests that omit them.
    Exact netloc equality prevents subdomain-lookalike attacks
    (e.g. example.org.attacker.com does not match example.org).
    """
    host = request.headers.get("host", "")
    scheme = request.url.scheme

    origin = request.headers.get("origin")
    if origin:
        p = urllib.parse.urlparse(origin)
        return p.scheme == scheme and p.netloc == host

    referer = request.headers.get("referer")
    if referer:
        p = urllib.parse.urlparse(referer)
        return p.netloc == host

    return True
