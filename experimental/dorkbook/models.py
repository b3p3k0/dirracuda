"""Data models and constants for the Dorkbook sidecar module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PROTOCOL_SMB = "SMB"
PROTOCOL_FTP = "FTP"
PROTOCOL_HTTP = "HTTP"
PROTOCOLS = (PROTOCOL_SMB, PROTOCOL_FTP, PROTOCOL_HTTP)

ROW_KIND_BUILTIN = "builtin"
ROW_KIND_CUSTOM = "custom"
ROW_KINDS = (ROW_KIND_BUILTIN, ROW_KIND_CUSTOM)


@dataclass(frozen=True)
class BuiltinDork:
    """Read-only default Dorkbook recipe."""

    builtin_key: str
    protocol: str
    nickname: str
    query: str
    notes: Optional[str] = None


@dataclass(frozen=True)
class DorkbookEntry:
    """Dorkbook entry row model."""

    entry_id: int
    protocol: str
    nickname: str
    query: str
    notes: str
    row_kind: str
    builtin_key: Optional[str]
    created_at: str
    updated_at: str


DEFAULT_BUILTIN_DORKS = (
    BuiltinDork(
        builtin_key="builtin_smb_default",
        protocol=PROTOCOL_SMB,
        nickname="Default SMB Dork",
        query="smb authentication: disabled",
        notes="Shipped default SMB dork.",
    ),
    BuiltinDork(
        builtin_key="builtin_ftp_default",
        protocol=PROTOCOL_FTP,
        nickname="Default FTP Dork",
        query='port:21 "230 Login successful"',
        notes="Shipped default FTP dork.",
    ),
    BuiltinDork(
        builtin_key="builtin_http_default",
        protocol=PROTOCOL_HTTP,
        nickname="Default HTTP Dork",
        query='http.title:"Index of /"',
        notes="Shipped default HTTP dork.",
    ),
)


class DorkbookError(Exception):
    """Base Dorkbook exception."""


class DuplicateEntryError(DorkbookError):
    """Raised when an entry duplicates a query within a protocol."""


class ReadOnlyEntryError(DorkbookError):
    """Raised when a caller attempts to mutate a read-only builtin row."""

