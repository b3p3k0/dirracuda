from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

AUTH_UNAUTHORIZED    = "auth_unauthorized"
AUTH_FORBIDDEN       = "auth_forbidden"
NOT_FOUND            = "not_found"
QUERY_INVALID        = "query_invalid"
CLIENT_ERROR         = "client_error"
SERVER_ERROR         = "server_error"
NETWORK_ERROR        = "network_error"
RESPONSE_PARSE_ERROR = "response_parse_error"


@dataclass
class ApiError:
    reason_code: str
    status_code: Optional[int]
    message: str


@dataclass
class ClientResult:
    ok: bool
    data: Optional[Any]
    error: Optional[ApiError]


@dataclass
class SearchResultItem:
    ip_address: str
    port: int
    protocol: str
    transport_protocol: Optional[str]
    banner: Optional[str]
    scan_time: Optional[str]
    source_json: str


@dataclass
class SearchPage:
    items: List[SearchResultItem]
    next_cursor: Optional[str]
    total_hits: Optional[int]  # SDK returns float; coerced to int in parser


@dataclass
class CreditBalance:
    balance: int
    resets_at: Optional[str]  # user credits only; None for org credits
