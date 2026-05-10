"""C2: experimental/webui/auth.py tests."""

import json
import pytest

from experimental.webui.auth import (
    PBKDF2_ALGORITHM,
    PBKDF2_ITERATIONS,
    MAX_PASSWORD_BYTES,
    credential_exists,
    set_password,
    verify_password,
)


def test_set_and_verify_password(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "correct-horse", p)
    assert verify_password("admin", "correct-horse", p) is True


def test_wrong_password_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "correct-horse", p)
    assert verify_password("admin", "wrong-password", p) is False


def test_missing_user_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "password", p)
    assert verify_password("nobody", "password", p) is False


def test_unique_salts_per_set(tmp_path):
    p = tmp_path / "creds.json"
    set_password("user1", "pass", p)
    salt1 = json.loads(p.read_text())["user1"]["salt"]
    set_password("user2", "pass", p)
    salt2 = json.loads(p.read_text())["user2"]["salt"]
    assert salt1 != salt2


def test_iterations_at_minimum(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "pass", p)
    record = json.loads(p.read_text())["admin"]
    assert record["iterations"] >= PBKDF2_ITERATIONS


def test_algorithm_field_stored(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "pass", p)
    record = json.loads(p.read_text())["admin"]
    assert record["algorithm"] == PBKDF2_ALGORITHM


def test_unknown_algorithm_returns_false(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "pass", p)
    data = json.loads(p.read_text())
    data["admin"]["algorithm"] = "argon2id"
    p.write_text(json.dumps(data))
    assert verify_password("admin", "pass", p) is False


def test_overlong_password_raises_on_set(tmp_path):
    p = tmp_path / "creds.json"
    long_pw = "x" * (MAX_PASSWORD_BYTES + 1)
    with pytest.raises(ValueError, match="password exceeds"):
        set_password("admin", long_pw, p)


def test_overlong_password_returns_false_on_verify(tmp_path):
    p = tmp_path / "creds.json"
    set_password("admin", "short", p)
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
    set_password("admin", "pass", p)
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
    set_password("admin", "pass", p)
    data = json.loads(p.read_text())
    data["admin"]["salt"] = "not-valid-hex-ZZZZ"
    p.write_text(json.dumps(data))
    assert verify_password("admin", "pass", p) is False


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
    set_password("admin", "pass", p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600
