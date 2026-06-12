from __future__ import annotations

from typing import Optional

PROTOCOL_BASELINES = {
    "FTP":  "host.services:(protocol=FTP and port=21)",
    "HTTP": "host.services:(protocol=HTTP and port=80)",
    "SMB":  "host.services:(protocol=SMB and port=445)",
}

PROTOCOL_EXTRA_FIELDS: dict[str, list[str]] = {
    "FTP": [
        "host.services.ftp.banner",
        "host.services.ftp.implicit_tls",
        "host.services.ftp.status_code",
    ],
    "HTTP": [
        "host.services.endpoints.http.headers",
        "host.services.endpoints.http.body_hash_sha256",
    ],
    "SMB": [
        "host.services.software.product",
        "host.services.software.version",
    ],
}


def get_extra_fields(protocol: str) -> list[str]:
    return list(PROTOCOL_EXTRA_FIELDS.get(protocol.upper(), []))


def build_query(protocol: str, query_hours: Optional[int] = None) -> str:
    proto = protocol.upper()
    if proto not in PROTOCOL_BASELINES:
        valid = ", ".join(sorted(PROTOCOL_BASELINES))
        raise ValueError(f"Unsupported protocol. Valid choices: {valid}")
    query = PROTOCOL_BASELINES[proto]
    if query_hours is not None and query_hours > 0:
        query += f' and host.services.scan_time > "now-{query_hours}h"'
    return query
