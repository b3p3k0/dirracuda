"""Data models and constants for the Dorkbook sidecar module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from experimental.dorkbook.catalog import DIRECTORY_IDEAS

PROTOCOL_SMB = "SMB"
PROTOCOL_FTP = "FTP"
PROTOCOL_HTTP = "HTTP"
PROTOCOLS = (PROTOCOL_SMB, PROTOCOL_FTP, PROTOCOL_HTTP)

PROVIDER_SHODAN = "shodan"
PROVIDER_SELF_HOSTED = "self_hosted"
PROVIDERS = (PROVIDER_SHODAN, PROVIDER_SELF_HOSTED)
DEFAULT_TOPIC = "General"

ROW_KIND_BUILTIN = "builtin"
ROW_KIND_CUSTOM = "custom"
ROW_KINDS = (ROW_KIND_BUILTIN, ROW_KIND_CUSTOM)


@dataclass(frozen=True)
class BuiltinDork:
    """Read-only default Dorkbook dork."""

    builtin_key: str
    protocol: Optional[str]
    nickname: str
    query: str
    notes: Optional[str] = None
    provider: str = PROVIDER_SHODAN
    topic: str = DEFAULT_TOPIC


@dataclass(frozen=True)
class DorkbookEntry:
    """Dorkbook entry row model."""

    entry_id: int
    protocol: Optional[str]
    nickname: str
    query: str
    notes: str
    row_kind: str
    builtin_key: Optional[str]
    created_at: str
    updated_at: str
    provider: str = PROVIDER_SHODAN
    topic: str = DEFAULT_TOPIC


_ORIGINAL_BUILTIN_DORKS = (
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


def _directory_builtins() -> tuple[BuiltinDork, ...]:
    """Build only our authored catalog forms, never translate user queries."""
    dorks = [BuiltinDork(
        builtin_key="builtin_self_hosted_default",
        protocol=None,
        provider=PROVIDER_SELF_HOSTED,
        nickname="Default Self-hosted Search Dork",
        query='intitle:"Index of /"',
        notes=("A broad directory-title search. Add a folder name or filename clue. "
               "SearXNG and DeGoog pass search syntax to their upstream engines."),
    )]
    for idea in DIRECTORY_IDEAS:
        for provider, protocol, query in (
            (PROVIDER_SHODAN, PROTOCOL_HTTP,
             f'http.title:"Index of /" http.html:"{idea.clue}"'),
            (PROVIDER_SELF_HOSTED, None,
             f'intitle:"Index of /" "{idea.clue}"'),
        ):
            dorks.append(BuiltinDork(
                builtin_key=f"builtin_{provider}_{idea.key}",
                protocol=protocol,
                provider=provider,
                nickname=idea.nickname,
                query=query,
                notes=idea.notes,
                topic=idea.topic,
            ))
    for key, title, nickname, notes in (
        ("directory_listing", "Directory listing", "Directories — Directory listing titles",
         "Finds titles such as Directory listing for /books/. "
         "Try adding an ebook, video, or music clue."),
        ("index_of", "Index of", "Directories — broader Index of titles",
         "Removes the slash from the default title clue, including titles like Index of books. "
         "Search engines may treat punctuation alike, so results can overlap the default."),
    ):
        for provider, protocol, title_filter in (
            (PROVIDER_SHODAN, PROTOCOL_HTTP, "http.title"),
            (PROVIDER_SELF_HOSTED, None, "intitle"),
        ):
            dorks.append(BuiltinDork(
                builtin_key=f"builtin_{provider}_{key}",
                protocol=protocol,
                provider=provider,
                nickname=nickname,
                query=f'{title_filter}:"{title}"',
                notes=notes,
            ))
    return tuple(dorks)


DEFAULT_BUILTIN_DORKS = _ORIGINAL_BUILTIN_DORKS + _directory_builtins()


class DorkbookError(Exception):
    """Base Dorkbook exception."""


class DuplicateEntryError(DorkbookError):
    """Raised when an entry duplicates a query within a protocol."""


class ReadOnlyEntryError(DorkbookError):
    """Raised when a caller attempts to mutate a read-only builtin row."""
