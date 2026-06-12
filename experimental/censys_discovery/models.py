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


RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE    = "done"
RUN_STATUS_ERROR   = "error"


@dataclass(repr=False)
class CensysRunOptions:
    pat: str
    protocol: str
    query_hours: Optional[int] = None
    max_pages: int = 5
    page_size: int = 100
    org_id: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"CensysRunOptions(protocol={self.protocol!r}, query_hours={self.query_hours}, "
            f"max_pages={self.max_pages}, page_size={self.page_size}, "
            f"org_id={self.org_id!r}, pat=<redacted>)"
        )


@dataclass
class CensysRunResult:
    ok: bool
    run_id: Optional[int]
    fetched_count: int
    deduped_count: int
    status: str
    error: Optional[str]
