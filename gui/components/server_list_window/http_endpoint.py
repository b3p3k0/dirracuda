"""HTTP endpoint resolution shared by Server List copy and browse actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


def _value(mapping: Mapping[str, Any], key: str) -> Any:
    value = mapping.get(key)
    if isinstance(value, str):
        value = value.strip()
    return value if value not in (None, "") else None


def normalize_http_path(value: Any) -> str:
    """Normalize a saved HTTP path while preserving a trailing slash."""
    path = str(value or "").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    return path


@dataclass(frozen=True)
class HttpBrowseEndpoint:
    """Resolved HTTP endpoint with IP identity and request URL metadata."""

    ip_address: str
    port: int
    scheme: str
    request_host: Optional[str]
    initial_path: str

    @property
    def authority(self) -> str:
        return self.request_host or self.ip_address

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.authority}:{self.port}{self.initial_path}"


def resolve_http_endpoint(
    ip_address: str,
    row_data: Optional[Mapping[str, Any]] = None,
    *,
    db_reader=None,
) -> HttpBrowseEndpoint:
    """Resolve DB-authoritative HTTP endpoint metadata with row fallbacks."""
    row = dict(row_data or {})
    row_port = _value(row, "port")
    protocol_server_id = _value(row, "protocol_server_id")

    detail = None
    if db_reader is not None:
        try:
            detail = db_reader.get_http_server_detail(
                ip_address,
                protocol_server_id=protocol_server_id,
                port=row_port,
            )
        except Exception:
            detail = None
    detail = detail or {}

    port_raw = _value(detail, "port") or row_port or 80
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 80
    if port < 1 or port > 65535:
        port = 80

    scheme = str(
        _value(detail, "scheme")
        or _value(row, "scheme")
        or ("https" if port == 443 else "http")
    ).lower()
    if scheme not in {"http", "https"}:
        scheme = "https" if port == 443 else "http"

    request_host = _value(detail, "probe_host") or _value(row, "probe_host")
    initial_path = normalize_http_path(
        _value(detail, "probe_path") or _value(row, "probe_path") or "/"
    )
    return HttpBrowseEndpoint(
        ip_address=str(ip_address or "").strip(),
        port=port,
        scheme=scheme,
        request_host=str(request_host).strip() if request_host else None,
        initial_path=initial_path,
    )


def resolve_http_target(target: Mapping[str, Any], *, db_reader=None) -> HttpBrowseEndpoint:
    """Resolve endpoint metadata from a Server List target wrapper."""
    row_data = dict(target.get("data") or {})
    for key in (
        "protocol_server_id",
        "port",
        "scheme",
        "probe_host",
        "probe_path",
    ):
        if _value(row_data, key) is None and _value(target, key) is not None:
            row_data[key] = target.get(key)
    return resolve_http_endpoint(
        str(target.get("ip_address") or "").strip(),
        row_data,
        db_reader=db_reader,
    )


def open_http_server_browser(
    endpoint: HttpBrowseEndpoint,
    *,
    parent,
    banner=None,
    config_path=None,
    db_reader=None,
    theme=None,
    settings_manager=None,
) -> None:
    """Open the HTTP explorer at the endpoint's saved host and path."""
    from gui.components.unified_browser_window import open_ftp_http_browser

    open_ftp_http_browser(
        "H",
        parent=parent,
        ip_address=endpoint.ip_address,
        port=endpoint.port,
        scheme=endpoint.scheme,
        request_host=endpoint.request_host,
        initial_path=endpoint.initial_path,
        banner=banner,
        config_path=config_path,
        db_reader=db_reader,
        theme=theme,
        settings_manager=settings_manager,
    )


__all__ = [
    "HttpBrowseEndpoint",
    "normalize_http_path",
    "open_http_server_browser",
    "resolve_http_endpoint",
    "resolve_http_target",
]
