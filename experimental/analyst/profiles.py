"""Named model-server profiles on the Analyst sidecar database.

N1 stores a profile for any endpoint.  It does not make a remote one reachable:
``OllamaClient`` refuses a non-loopback connection under D17 until N3 writes the
transport policy.  The columns TLS, Keymaster, and egress consent need are
created here so those cards add behaviour rather than schema.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final

from .endpoint import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCHEME,
    AddressClass,
    Endpoint,
    parse_endpoint,
)
from .store import initialize_database, run_immediate, run_read


MAX_PROFILE_NAME_CHARS: Final = 64
MAX_PROFILES: Final = 64
DEFAULT_PROFILE_NAME: Final = "Local Ollama"

_SHA256_CHARS: Final = 64
_COLUMNS: Final = (
    "profile_id, name, scheme, host, port, backend_kind, backend_detected, "
    "keymaster_key_id, cert_fingerprint, plaintext_ack, consent_muted, "
    "created_at_utc, last_used_utc"
)


class ProfileError(ValueError):
    """A supplied profile value is outside the N1 profile contract."""


class BackendKind(str, Enum):
    """The two transports of the remote-backends contract §3."""

    OLLAMA = "ollama"
    OPENAI_COMPAT = "openai"


@dataclass(frozen=True, slots=True)
class ServerProfile:
    """One stored model-server profile."""

    profile_id: int
    name: str
    scheme: str
    host: str
    port: int
    backend_kind: BackendKind
    backend_detected: bool
    keymaster_key_id: int | None
    cert_fingerprint: str | None
    plaintext_ack: bool
    consent_muted: bool
    created_at_utc: str
    last_used_utc: str | None

    @property
    def endpoint(self) -> Endpoint:
        """Return this profile's validated endpoint."""
        return Endpoint(scheme=self.scheme, host=self.host, port=self.port)

    @property
    def endpoint_url(self) -> str:
        """Return this profile's canonical `scheme://host:port` string."""
        return self.endpoint.base_url

    @property
    def address_class(self) -> AddressClass:
        """Return the host classification, without resolving any name."""
        return self.endpoint.address_class

    @property
    def is_loopback(self) -> bool:
        """Return whether this profile names the local machine."""
        return self.endpoint.is_loopback

    @property
    def is_reachable_now(self) -> bool:
        """Return whether D17 currently permits contacting this profile."""
        return self.is_loopback


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_name(value: object) -> str:
    if type(value) is not str:
        raise ProfileError("profile name must be a string")
    name = value.strip()
    if not 1 <= len(name) <= MAX_PROFILE_NAME_CHARS:
        raise ProfileError(
            f"profile name must be 1-{MAX_PROFILE_NAME_CHARS} characters"
        )
    return name


def _require_backend(value: object) -> BackendKind:
    if isinstance(value, BackendKind):
        return value
    try:
        return BackendKind(value)
    except ValueError:
        raise ProfileError("backend kind is not a known transport") from None


def _require_fingerprint(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ProfileError("certificate fingerprint must be a string or None")
    lowered = value.strip().lower().replace(":", "")
    if len(lowered) != _SHA256_CHARS or any(
        char not in "0123456789abcdef" for char in lowered
    ):
        raise ProfileError("certificate fingerprint must be a SHA-256 hex digest")
    return lowered


def _require_key_id(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is bool or type(value) is not int or value <= 0:
        raise ProfileError("keymaster key id must be a positive integer or None")
    return value


def _require_profile_id(value: object) -> int:
    if type(value) is bool or type(value) is not int or value <= 0:
        raise ProfileError("profile id must be a positive integer")
    return value


def _row_to_profile(row: sqlite3.Row) -> ServerProfile:
    return ServerProfile(
        profile_id=int(row["profile_id"]),
        name=str(row["name"]),
        scheme=str(row["scheme"]),
        host=str(row["host"]),
        port=int(row["port"]),
        backend_kind=BackendKind(str(row["backend_kind"])),
        backend_detected=bool(row["backend_detected"]),
        keymaster_key_id=(
            None if row["keymaster_key_id"] is None else int(row["keymaster_key_id"])
        ),
        cert_fingerprint=(
            None if row["cert_fingerprint"] is None else str(row["cert_fingerprint"])
        ),
        plaintext_ack=bool(row["plaintext_ack"]),
        consent_muted=bool(row["consent_muted"]),
        created_at_utc=str(row["created_at_utc"]),
        last_used_utc=(
            None if row["last_used_utc"] is None else str(row["last_used_utc"])
        ),
    )


def create_profile(
    name: str,
    endpoint: str | Endpoint | None = None,
    *,
    scheme: str = DEFAULT_SCHEME,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    backend_kind: BackendKind | str = BackendKind.OLLAMA,
    backend_detected: bool = False,
    keymaster_key_id: int | None = None,
    cert_fingerprint: str | None = None,
    plaintext_ack: bool = False,
    consent_muted: bool = False,
    now_utc: str | None = None,
    path: Path | None = None,
) -> ServerProfile:
    """Create one named profile.  A non-loopback endpoint is stored, not dialled."""
    resolved = _require_name(name)
    address = (
        parse_endpoint(endpoint)
        if endpoint is not None
        else Endpoint(scheme=scheme, host=host, port=port)
    )
    kind = _require_backend(backend_kind)
    fingerprint = _require_fingerprint(cert_fingerprint)
    key_id = _require_key_id(keymaster_key_id)
    created = now_utc if type(now_utc) is str and now_utc else _now_utc()
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> ServerProfile:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM analyst_llm_profile"
        ).fetchone()
        if int(total["n"]) >= MAX_PROFILES:
            raise ProfileError(f"at most {MAX_PROFILES} profiles are supported")
        cursor = conn.execute(
            "INSERT INTO analyst_llm_profile("
            "name,scheme,host,port,backend_kind,backend_detected,"
            "keymaster_key_id,cert_fingerprint,plaintext_ack,consent_muted,"
            "created_at_utc,last_used_utc) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                resolved,
                address.scheme,
                address.host,
                address.port,
                kind.value,
                int(bool(backend_detected)),
                key_id,
                fingerprint,
                int(bool(plaintext_ack)),
                int(bool(consent_muted)),
                created,
            ),
        )
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM analyst_llm_profile WHERE profile_id=?",
            (int(cursor.lastrowid),),
        ).fetchone()
        return _row_to_profile(row)

    try:
        return run_immediate(operation, path=path)
    except sqlite3.IntegrityError as exc:
        raise ProfileError(
            "a profile with that name or endpoint already exists"
        ) from exc


def list_profiles(*, path: Path | None = None) -> tuple[ServerProfile, ...]:
    """Return every stored profile, oldest first."""
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> tuple[ServerProfile, ...]:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM analyst_llm_profile ORDER BY profile_id"
        ).fetchall()
        return tuple(_row_to_profile(row) for row in rows)

    return run_read(operation, path=path)


def get_profile(
    profile_id: int, *, path: Path | None = None,
) -> ServerProfile | None:
    """Return one profile by id, or None when it does not exist."""
    wanted = _require_profile_id(profile_id)
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> ServerProfile | None:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM analyst_llm_profile WHERE profile_id=?",
            (wanted,),
        ).fetchone()
        return None if row is None else _row_to_profile(row)

    return run_read(operation, path=path)


_UPDATABLE: Final = {
    "name": _require_name,
    "backend_kind": lambda value: _require_backend(value).value,
    "backend_detected": lambda value: int(bool(value)),
    "keymaster_key_id": _require_key_id,
    "cert_fingerprint": _require_fingerprint,
    "plaintext_ack": lambda value: int(bool(value)),
    "consent_muted": lambda value: int(bool(value)),
}


def update_profile(
    profile_id: int,
    *,
    endpoint: str | Endpoint | None = None,
    path: Path | None = None,
    **fields: object,
) -> ServerProfile:
    """Update one profile in place and return the stored result."""
    wanted = _require_profile_id(profile_id)
    unknown = set(fields) - set(_UPDATABLE)
    if unknown:
        raise ProfileError(f"unknown profile fields: {sorted(unknown)}")
    assignments: list[str] = []
    values: list[object] = []
    for column, coerce in _UPDATABLE.items():
        if column in fields:
            assignments.append(f"{column}=?")
            values.append(coerce(fields[column]))
    if endpoint is not None:
        address = parse_endpoint(endpoint)
        assignments.extend(("scheme=?", "host=?", "port=?"))
        values.extend((address.scheme, address.host, address.port))
    if not assignments:
        raise ProfileError("update_profile needs at least one field")
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> ServerProfile:
        cursor = conn.execute(
            f"UPDATE analyst_llm_profile SET {','.join(assignments)} "
            "WHERE profile_id=?",
            (*values, wanted),
        )
        if cursor.rowcount != 1:
            raise ProfileError("profile does not exist")
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM analyst_llm_profile WHERE profile_id=?",
            (wanted,),
        ).fetchone()
        return _row_to_profile(row)

    try:
        return run_immediate(operation, path=path)
    except sqlite3.IntegrityError as exc:
        raise ProfileError(
            "a profile with that name or endpoint already exists"
        ) from exc


def touch_last_used(
    profile_id: int, *, now_utc: str | None = None, path: Path | None = None,
) -> ServerProfile:
    """Record that a profile was used, without changing anything else."""
    wanted = _require_profile_id(profile_id)
    stamp = now_utc if type(now_utc) is str and now_utc else _now_utc()
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> ServerProfile:
        cursor = conn.execute(
            "UPDATE analyst_llm_profile SET last_used_utc=? WHERE profile_id=?",
            (stamp, wanted),
        )
        if cursor.rowcount != 1:
            raise ProfileError("profile does not exist")
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM analyst_llm_profile WHERE profile_id=?",
            (wanted,),
        ).fetchone()
        return _row_to_profile(row)

    return run_immediate(operation, path=path)


def delete_profile(profile_id: int, *, path: Path | None = None) -> None:
    """Delete one profile.  A profile referenced by a run is kept."""
    wanted = _require_profile_id(profile_id)
    initialize_database(path)

    def operation(conn: sqlite3.Connection) -> None:
        used = conn.execute(
            "SELECT 1 FROM analyst_runs WHERE profile_id=? LIMIT 1", (wanted,)
        ).fetchone()
        if used is not None:
            raise ProfileError("profile is referenced by a run and cannot be deleted")
        cursor = conn.execute(
            "DELETE FROM analyst_llm_profile WHERE profile_id=?", (wanted,)
        )
        if cursor.rowcount != 1:
            raise ProfileError("profile does not exist")

    return run_immediate(operation, path=path)


def ensure_default_profile(*, path: Path | None = None) -> ServerProfile:
    """Return the loopback Ollama profile, creating it on first use.

    Every existing install behaves as though this profile had always been
    selected, so creating it changes nothing about how a run executes.
    """
    for profile in list_profiles(path=path):
        if profile.is_loopback and profile.backend_kind is BackendKind.OLLAMA:
            return profile
    try:
        return create_profile(DEFAULT_PROFILE_NAME, path=path)
    except ProfileError:
        for profile in list_profiles(path=path):
            if profile.is_loopback:
                return profile
        raise


__all__ = [
    "BackendKind",
    "DEFAULT_PROFILE_NAME",
    "MAX_PROFILES",
    "MAX_PROFILE_NAME_CHARS",
    "ProfileError",
    "ServerProfile",
    "create_profile",
    "delete_profile",
    "ensure_default_profile",
    "get_profile",
    "list_profiles",
    "touch_last_used",
    "update_profile",
]
