"""C2: experimental/webui/auth.py tests."""

import json
import os
import pytest

import experimental.webui.auth as auth_module
from experimental.webui.auth import (
    BLOCKLIST_MIN_SIZE,
    PASSWORD_MIN_LENGTH,
    PBKDF2_ALGORITHM,
    PBKDF2_ITERATIONS,
    MAX_PASSWORD_BYTES,
    BlocklistUnavailableError,
    credential_exists,
    get_credential_usernames,
    set_password,
    validate_password_policy,
    verify_password,
    _load_blocklist,
)

_VALID_PW = "correct-horse-battery"  # 21 chars; not in blocklist


def test_set_and_verify_password(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    assert verify_password("admin", _VALID_PW, p) is True


def test_wrong_password_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    assert verify_password("admin", "wrong-password", p) is False


def test_missing_user_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    assert verify_password("nobody", _VALID_PW, p) is False


def test_unique_salts_per_set(tmp_path):
    p = tmp_path / "creds.json"
    set_password("user1", _VALID_PW, p)
    salt1 = json.loads(p.read_text())["user1"]["salt"]
    set_password("user2", _VALID_PW, p)
    salt2 = json.loads(p.read_text())["user2"]["salt"]
    assert salt1 != salt2


def test_iterations_at_minimum(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    record = json.loads(p.read_text())["admin"]
    assert record["iterations"] >= PBKDF2_ITERATIONS


def test_algorithm_field_stored(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    record = json.loads(p.read_text())["admin"]
    assert record["algorithm"] == PBKDF2_ALGORITHM


def test_unknown_algorithm_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    data = json.loads(p.read_text())
    data["admin"]["algorithm"] = "argon2id"
    p.write_text(json.dumps(data))
    assert verify_password("admin", _VALID_PW, p) is False


def test_overlong_password_raises_on_set(tmp_path):
    p = tmp_path / "creds.json"
    long_pw = "x" * (MAX_PASSWORD_BYTES + 1)
    with pytest.raises(ValueError, match="password exceeds"):
        set_password("admin", long_pw, p)


def test_overlong_password_returns_false_on_verify(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    long_pw = "x" * (MAX_PASSWORD_BYTES + 1)
    assert verify_password("admin", long_pw, p) is False


def test_empty_username_raises(tmp_path):
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match="empty"):
        set_password("", "pass", p)


def test_whitespace_username_raises(tmp_path):
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match="whitespace"):
        set_password(" admin", "pass", p)


def test_credential_exists_true(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    assert credential_exists(p) is True


def test_credential_exists_false(tmp_path):
    p = tmp_path / "no_creds.json"
    assert credential_exists(p) is False


def test_no_plaintext_in_cred_file(tmp_path):
    p = tmp_path / "creds.json"
    password = "super-secret-password-123"
    set_password("admin", password, p)
    content = p.read_text()
    assert password not in content


def test_malformed_hex_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    data = json.loads(p.read_text())
    data["admin"]["salt"] = "not-valid-hex-ZZZZ"
    p.write_text(json.dumps(data))
    assert verify_password("admin", _VALID_PW, p) is False


def test_non_string_username_raises(tmp_path):
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match="str"):
        set_password(123, "pass", p)


def test_del_char_username_raises(tmp_path):
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match="control"):
        set_password("admin\x7f", "pass", p)


def test_file_mode_restricted(tmp_path):
    import stat
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# O2 — Password policy and blocklist tests
# ---------------------------------------------------------------------------

def test_set_password_too_short(tmp_path, monkeypatch):
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match=str(PASSWORD_MIN_LENGTH) + " characters"):
        set_password("admin", "tooshort12345", p)


def test_set_password_exactly_15(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "aaaaabbbbbccccc", p)
    assert verify_password("admin", "aaaaabbbbbccccc", p) is True


def test_set_password_blocklisted(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_module, "_BLOCKLIST", frozenset({"blockedtestpassword123"}))
    p = tmp_path / "creds.json"
    with pytest.raises(ValueError, match="too common"):
        set_password("admin", "blockedtestpassword123", p)


def test_set_password_passphrase(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "correct horse battery staple", p)
    assert verify_password("admin", "correct horse battery staple", p) is True


def test_set_password_64_chars_accepted(tmp_path):
    pw = "x" * 64
    p = tmp_path / "creds.json"
    set_password("admin", pw, p)
    assert verify_password("admin", pw, p) is True


def test_set_password_blocklist_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_module, "_BLOCKLIST", None)
    p = tmp_path / "creds.json"
    with pytest.raises(BlocklistUnavailableError):
        set_password("admin", _VALID_PW, p)


def test_blocklist_unreadable_returns_none(monkeypatch):
    from pathlib import Path

    def _raise(*a, **kw):
        raise PermissionError("no perm")

    monkeypatch.setattr(Path, "read_text", _raise)
    assert _load_blocklist() is None


def test_blocklist_is_directory_returns_none(monkeypatch):
    from pathlib import Path

    def _raise(*a, **kw):
        raise IsADirectoryError("is dir")

    monkeypatch.setattr(Path, "read_text", _raise)
    assert _load_blocklist() is None


def test_blocklist_empty_file_returns_none(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: "")
    assert _load_blocklist() is None


def test_blocklist_undersized_returns_none(monkeypatch):
    from pathlib import Path
    content = "\n".join(f"pw{i:05d}" for i in range(2999))
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: content)
    assert _load_blocklist() is None


def test_blocklist_exactly_min_size_ok(monkeypatch):
    from pathlib import Path
    content = "\n".join(f"pw{i:05d}" for i in range(BLOCKLIST_MIN_SIZE))
    monkeypatch.setattr(Path, "read_text", lambda *a, **kw: content)
    result = _load_blocklist()
    assert result is not None
    assert len(result) == BLOCKLIST_MIN_SIZE


def test_blocklist_unavailable_is_not_value_error(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_module, "_BLOCKLIST", None)
    p = tmp_path / "creds.json"
    exc = None
    try:
        set_password("admin", _VALID_PW, p)
    except BlocklistUnavailableError as e:
        exc = e
    assert exc is not None
    assert not isinstance(exc, ValueError)


def test_verify_password_preexisting_weak_still_works(tmp_path):
    import hashlib
    p = tmp_path / "creds.json"
    salt = os.urandom(32)
    pw_bytes = b"weak"
    dk = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt, PBKDF2_ITERATIONS)
    creds = {
        "admin": {
            "algorithm": PBKDF2_ALGORITHM,
            "iterations": PBKDF2_ITERATIONS,
            "salt": salt.hex(),
            "hash": dk.hex(),
        }
    }
    p.write_text(json.dumps(creds))
    os.chmod(p, 0o600)
    assert verify_password("admin", "weak", p) is True


def test_validate_password_policy_exported():
    assert callable(validate_password_policy)
    with pytest.raises((ValueError, BlocklistUnavailableError)):
        validate_password_policy("short")


# ---------------------------------------------------------------------------
# O4 — Credential file permission hardening
# ---------------------------------------------------------------------------

# POSIX-only: Windows chmod does not enforce mode bits
_posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX-only permission enforcement")


@_posix_only
def test_load_creds_raises_on_world_readable(tmp_path):
    from experimental.webui.auth import CredentialError, _load_creds
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    os.chmod(p, 0o644)
    with pytest.raises(CredentialError, match="unsafe permissions"):
        _load_creds(p)


@_posix_only
def test_verify_password_returns_false_on_bad_permissions(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    os.chmod(p, 0o644)
    assert verify_password("admin", _VALID_PW, p) is False


@_posix_only
def test_set_password_raises_on_bad_permissions_existing_file(tmp_path):
    from experimental.webui.auth import CredentialError
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    os.chmod(p, 0o644)
    with pytest.raises(CredentialError, match="unsafe permissions"):
        set_password("admin", _VALID_PW, p)


@_posix_only
def test_credential_exists_raises_on_bad_permissions(tmp_path):
    from experimental.webui.auth import CredentialError
    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)
    os.chmod(p, 0o644)
    with pytest.raises(CredentialError):
        credential_exists(p)


@_posix_only
def test_check_creds_permissions_oserror_raises_credential_error(tmp_path, monkeypatch):
    """stat() OSError in _check_creds_permissions maps to CredentialError.

    Test calls _check_creds_permissions directly (not _load_creds) so the mock
    only needs to intercept the single stat() call inside the helper — avoiding
    the exists() call in _load_creds which also calls stat() internally.
    """
    from pathlib import Path
    from experimental.webui.auth import CredentialError, _check_creds_permissions

    p = tmp_path / "creds.json"
    set_password("admin", _VALID_PW, p)

    _original_stat = Path.stat

    def _bad_stat(self, *args, **kwargs):
        if self == p:
            raise PermissionError("no access")
        return _original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _bad_stat)
    with pytest.raises(CredentialError, match="Cannot verify permissions"):
        _check_creds_permissions(p)
