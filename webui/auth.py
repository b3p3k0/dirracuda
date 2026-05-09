"""Web UI credential store: PBKDF2-HMAC-SHA256 password hashing and verification."""

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional

from webui.config import _atomic_write_json


PBKDF2_ITERATIONS = 600_000
PBKDF2_ALGORITHM = "pbkdf2_hmac_sha256"
SALT_BYTES = 32
MAX_PASSWORD_BYTES = 1024
MAX_USERNAME_BYTES = 128


class CredentialError(Exception):
    """Raised for credential store access failures."""


def _creds_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    return Path.home() / ".dirracuda" / "conf" / "webui_creds.json"


def _load_creds(path: Optional[Path] = None) -> dict:
    p = _creds_path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _validate_username(username: str) -> None:
    if not isinstance(username, str):
        raise ValueError("username must be a str")
    if not username:
        raise ValueError("username must not be empty")
    if username != username.strip():
        raise ValueError("username must not have leading or trailing whitespace")
    if any(ord(c) < 32 or ord(c) == 127 for c in username):
        raise ValueError("username must not contain control characters")
    if len(username.encode("utf-8")) > MAX_USERNAME_BYTES:
        raise ValueError(f"username exceeds {MAX_USERNAME_BYTES} bytes")


def set_password(username: str, password: str, path: Optional[Path] = None) -> None:
    """Hash and store a password for username.

    Raises ValueError for invalid username or overlong password (after UTF-8 encoding).
    """
    _validate_username(username)
    pw_bytes = password.encode("utf-8")
    if len(pw_bytes) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password exceeds {MAX_PASSWORD_BYTES} bytes after UTF-8 encoding"
        )
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, PBKDF2_ITERATIONS)
    creds = _load_creds(path)
    creds[username] = {
        "algorithm": PBKDF2_ALGORITHM,
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "hash": dk.hex(),
    }
    _atomic_write_json(creds, _creds_path(path))


def verify_password(username: str, password: str, path: Optional[Path] = None) -> bool:
    """Return True if password matches the stored credential for username.

    Returns False (never raises) for any failure: missing file, missing user,
    unknown algorithm, malformed hex, bool or non-int iterations, iterations below
    policy minimum, or overlong password.
    """
    try:
        pw_bytes = password.encode("utf-8")
        if len(pw_bytes) > MAX_PASSWORD_BYTES:
            return False
        creds = _load_creds(path)
        if username not in creds:
            return False
        record = creds[username]
        if not isinstance(record, dict):
            return False
        if record.get("algorithm") != PBKDF2_ALGORITHM:
            return False
        iterations = record.get("iterations")
        if isinstance(iterations, bool) or not isinstance(iterations, int):
            return False
        if iterations < PBKDF2_ITERATIONS:
            return False
        try:
            salt = bytes.fromhex(record["salt"])
            stored_hash = record["hash"]
            bytes.fromhex(stored_hash)
        except (KeyError, ValueError):
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, iterations)
        return hmac.compare_digest(dk.hex(), stored_hash)
    except Exception:
        return False


def credential_exists(path: Optional[Path] = None) -> bool:
    """Return True if the credential store exists and contains at least one entry."""
    return bool(_load_creds(path))
