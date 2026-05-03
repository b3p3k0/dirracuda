"""Shared sidecar-to-main-DB promotion helpers.

Sidecar browsers pass a lightweight prefill dict. This module turns that
prefill into the same manual-record payload used by Server List Browser, while
keeping DNS resolution and validation explicit at promotion time.
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import socket
from typing import Any, Callable, Dict, Optional

from gui.utils.probe_snapshot_summary import summarize_probe_snapshot


class SidecarPromotionError(ValueError):
    """Raised when a sidecar row cannot be safely promoted."""


Resolver = Callable[..., list]
CACHEABLE_PROBE_STATUSES = {"clean", "issue"}


def build_manual_record_payload(
    prefill: Dict[str, Any],
    *,
    resolver: Optional[Resolver] = None,
) -> Dict[str, Any]:
    """Convert a sidecar prefill dict into a DatabaseReader manual payload."""
    if not isinstance(prefill, dict):
        raise SidecarPromotionError("Promotion data is missing or invalid.")

    host_type = str(prefill.get("host_type") or "").strip().upper()
    if host_type not in {"S", "F", "H"}:
        raise SidecarPromotionError("Only SMB, FTP, and HTTP rows can be promoted.")

    source_host = str(prefill.get("host") or prefill.get("ip_address") or "").strip()
    ip_address = resolve_ipv4(source_host, resolver=resolver)

    payload: Dict[str, Any] = {
        "host_type": host_type,
        "ip_address": ip_address,
    }

    if host_type == "F":
        payload["port"] = _coerce_port(prefill.get("port"), default=21, label="FTP")
        return payload

    if host_type == "H":
        scheme = _normalize_scheme(prefill.get("scheme"))
        if scheme is None:
            port_seed = _coerce_optional_port(prefill.get("port"), label="HTTP")
            scheme = "https" if port_seed == 443 else "http"
            port = port_seed if port_seed is not None else 80
        else:
            port = _coerce_port(
                prefill.get("port"),
                default=443 if scheme == "https" else 80,
                label="HTTP",
            )

        payload["port"] = port
        payload["scheme"] = scheme

        probe_host = str(prefill.get("_probe_host_hint") or "").strip()
        if not probe_host and source_host and source_host != ip_address:
            probe_host = source_host
        if probe_host:
            payload["probe_host"] = probe_host

        probe_path = _normalize_probe_path(prefill.get("_probe_path_hint"))
        if probe_path:
            payload["probe_path"] = probe_path

    return payload


def promote_sidecar_prefill(db_reader: Any, prefill: Dict[str, Any]) -> Dict[str, Any]:
    """Write a sidecar prefill directly to the active main DB."""
    if db_reader is None:
        raise SidecarPromotionError("No main database is configured for this session.")

    payload = build_manual_record_payload(prefill)
    result = db_reader.upsert_manual_server_record(payload)
    probe_cache = build_probe_cache_payload(prefill)
    probe_cache_copied = False
    probe_snapshot = build_probe_snapshot_payload(prefill) if probe_cache is not None else None
    probe_snapshot_id = None
    probe_snapshot_copied = False
    if probe_snapshot is not None:
        probe_snapshot_id = db_reader.upsert_probe_snapshot_for_host(
            payload["ip_address"],
            payload["host_type"],
            probe_snapshot,
            protocol_server_id=result.get("protocol_server_id"),
            port=payload.get("port"),
            source=str(prefill.get("_probe_snapshot_source") or "sidecar"),
        )
        probe_snapshot_copied = probe_snapshot_id is not None
    if probe_cache is not None:
        if probe_snapshot_id is not None:
            probe_cache["latest_snapshot_id"] = probe_snapshot_id
        db_reader.upsert_probe_cache_for_host(
            payload["ip_address"],
            payload["host_type"],
            protocol_server_id=result.get("protocol_server_id"),
            port=payload.get("port"),
            **probe_cache,
        )
        probe_cache_copied = True
    try:
        db_reader.clear_cache()
    except Exception:
        pass
    return {
        "payload": payload,
        "result": result,
        "probe_cache": probe_cache,
        "probe_cache_copied": probe_cache_copied,
        "probe_snapshot_copied": probe_snapshot_copied,
        "probe_snapshot_id": probe_snapshot_id,
    }


def build_probe_cache_payload(prefill: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a DB probe-cache payload from a sidecar artifact, if cacheable."""
    if not isinstance(prefill, dict):
        return None
    raw = prefill.get("_probe_cache")
    if not isinstance(raw, dict):
        return None

    status = str(raw.get("status") or "").strip().lower()
    if status not in CACHEABLE_PROBE_STATUSES:
        return None

    snapshot = build_probe_snapshot_payload(prefill)
    snapshot_summary = summarize_probe_snapshot(snapshot) if snapshot else None
    preview = _normalize_preview(raw.get("preview"))
    host_type = str(prefill.get("host_type") or "").strip().upper()
    payload: Dict[str, Any] = {
        "status": status,
        "indicator_matches": _coerce_nonnegative_int(
            raw.get("indicator_matches"),
            default=0,
        ),
        "snapshot_path": None,
        "last_probe_at": _normalize_checked_at(raw.get("checked_at")),
    }

    if snapshot_summary and host_type == "H":
        display_entries = snapshot_summary["display_entries"]
        payload["accessible_dirs_list"] = ",".join(display_entries) if display_entries else None
        payload["accessible_dirs_count"] = len(snapshot_summary["directory_names"])
        payload["accessible_files_count"] = int(snapshot_summary["total_file_count"])
    elif snapshot_summary and host_type == "F":
        display_entries = snapshot_summary["display_entries"]
        payload["accessible_dirs_list"] = ",".join(display_entries) if display_entries else None
        payload["accessible_dirs_count"] = len(display_entries)
        payload["accessible_files_count"] = None
    elif preview:
        payload["accessible_dirs_list"] = preview
        payload["accessible_dirs_count"] = len(_split_preview(preview))
        payload["accessible_files_count"] = None
    else:
        payload["accessible_dirs_list"] = None
        payload["accessible_dirs_count"] = None
        payload["accessible_files_count"] = None

    return payload


def build_probe_snapshot_payload(prefill: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a full probe snapshot artifact from a sidecar prefill, if present."""
    if not isinstance(prefill, dict):
        return None
    raw = prefill.get("_probe_snapshot")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def format_promotion_success(promotion: Any) -> str:
    """Return a concise, user-facing success message."""
    payload = promotion.get("payload") if isinstance(promotion, dict) else {}
    result = promotion.get("result") if isinstance(promotion, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}

    host_type = str(result.get("host_type") or payload.get("host_type") or "").upper()
    type_label = {"S": "SMB", "F": "FTP", "H": "HTTP"}.get(host_type, "Host")
    operation = str(result.get("operation") or "upserted").lower()
    ip_address = str(payload.get("ip_address") or "").strip()
    endpoint = ip_address
    if host_type in {"F", "H"} and payload.get("port") not in (None, ""):
        endpoint = f"{ip_address}:{payload.get('port')}"

    probe_note = ""
    if isinstance(promotion, dict) and bool(promotion.get("probe_snapshot_copied")):
        probe_note = "\nProbe snapshot copied from sidecar."
    elif isinstance(promotion, dict) and bool(promotion.get("probe_cache_copied")):
        probe_note = "\nProbe summary copied from sidecar."

    return (
        f"{type_label} record {operation}: {endpoint}\n\n"
        "It may be hidden by active filters in Server List Browser."
        f"{probe_note}"
    )


def resolve_ipv4(host: str, *, resolver: Optional[Resolver] = None) -> str:
    """Return a literal IPv4 for host, resolving DNS if needed."""
    host = str(host or "").strip()
    if not host:
        raise SidecarPromotionError("Host is required for promotion.")

    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        parsed = None

    if parsed is not None:
        if parsed.version != 4:
            raise SidecarPromotionError(f"Host '{host}' is not an IPv4 address.")
        return str(parsed)

    lookup = resolver or socket.getaddrinfo
    try:
        infos = lookup(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        raise SidecarPromotionError(
            f"Could not resolve '{host}' to an IPv4 address."
        ) from exc

    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        if isinstance(sockaddr, tuple) and sockaddr:
            candidate = str(sockaddr[0]).strip()
            if candidate:
                return candidate

    raise SidecarPromotionError(f"Could not resolve '{host}' to an IPv4 address.")


def _normalize_scheme(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in {"http", "https"}:
        raise SidecarPromotionError("HTTP scheme must be either http or https.")
    return text


def _coerce_optional_port(value: Any, *, label: str) -> Optional[int]:
    if value in (None, ""):
        return None
    return _coerce_port(value, default=0, label=label)


def _coerce_port(value: Any, *, default: int, label: str) -> int:
    raw = default if value in (None, "") else value
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise SidecarPromotionError(
            f"{label} port must be an integer between 1 and 65535."
        ) from exc
    if port < 1 or port > 65535:
        raise SidecarPromotionError(
            f"{label} port must be an integer between 1 and 65535."
        )
    return port


def _normalize_probe_path(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not text.startswith("/"):
        text = "/" + text.lstrip("/")
    return text


def _coerce_nonnegative_int(value: Any, *, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, resolved)


def _normalize_preview(value: Any) -> Optional[str]:
    parts = _split_preview(value)
    return ",".join(parts) if parts else None


def _split_preview(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _normalize_checked_at(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text
