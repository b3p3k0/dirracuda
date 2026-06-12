"""Route integration tests for Keymaster API endpoints (C34)."""

import json
import re

import pytest
from fastapi.testclient import TestClient

import experimental.keymaster.store as km_store
from experimental.keymaster.models import InvalidPassphraseError
from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "km_tester"
_PASSWORD = "correct-horse-battery-staple"
_KEYMASTER_PASSPHRASE = "Km passphrase 123!"
_SENTINEL = "token=super-secret-keymaster-value"


def _raise_value_error(*_args, **_kwargs):
    raise ValueError(_SENTINEL)


def _assert_sanitized(response, caplog, status_code, payload):
    assert response.status_code == status_code
    assert response.json() == payload
    assert _SENTINEL not in response.text
    assert _SENTINEL not in caplog.text


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


@pytest.fixture
def main_config_path(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"shodan": {}}), encoding="utf-8")
    return p


@pytest.fixture
def km_db_path(tmp_path):
    path = tmp_path / "keymaster.db"
    km_store.init_db(path)
    conn = km_store.open_connection(path)
    try:
        km_store.set_secure_mode(conn, False)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def app(creds, cfg_no_tls, main_config_path, km_db_path):
    return create_app(
        cfg=cfg_no_tls,
        creds_path=creds,
        main_config_path=main_config_path,
        keymaster_db_path=km_db_path,
    )


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def _csrf(client):
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    assert m, "csrf-token meta tag not found"
    return m.group(1)


def _origin_header():
    return {"Origin": "http://testserver"}


def _create_key(client, csrf, *, label="test-key", api_key="abc123"):
    return client.post(
        "/api/keymaster/keys",
        json={"provider": "SHODAN", "label": label, "api_key": api_key, "notes": ""},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )


def _build_secure_logged_in_client(tmp_path, cfg_no_tls) -> TestClient:
    creds = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=creds)

    main_config_path = tmp_path / "config.json"
    main_config_path.write_text(json.dumps({"shodan": {}}), encoding="utf-8")

    km_db_path = tmp_path / "keymaster-secure.db"
    km_store.init_db(km_db_path)
    conn = km_store.open_connection(km_db_path)
    try:
        km_store.set_secure_mode(conn, True)
        km_store.configure_passphrase(conn, _KEYMASTER_PASSPHRASE)
        conn.commit()
    finally:
        conn.close()

    app = create_app(
        cfg=cfg_no_tls,
        creds_path=creds,
        main_config_path=main_config_path,
        keymaster_db_path=km_db_path,
    )
    client = TestClient(app, follow_redirects=False)
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------

def test_keymaster_status_requires_auth(client):
    r = client.get("/api/keymaster/status")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_keymaster_keys_get_requires_auth(client):
    r = client.get("/api/keymaster/keys")
    assert r.status_code == 303


def test_keymaster_unlock_requires_auth(client):
    r = client.post("/api/keymaster/unlock", json={"passphrase": "x"})
    assert r.status_code == 303


def test_keymaster_keys_post_requires_auth(client):
    r = client.post(
        "/api/keymaster/keys",
        json={"provider": "SHODAN", "label": "l", "api_key": "k", "notes": ""},
    )
    assert r.status_code == 303


def test_keymaster_patch_requires_auth(client):
    r = client.patch("/api/keymaster/keys/1", json={"label": "l", "notes": ""})
    assert r.status_code == 303


def test_keymaster_delete_requires_auth(client):
    r = client.delete("/api/keymaster/keys/1")
    assert r.status_code == 303


def test_keymaster_apply_requires_auth(client):
    r = client.post("/api/keymaster/apply", json={"key_id": 1})
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# CSRF guards
# ---------------------------------------------------------------------------

def test_keymaster_unlock_requires_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/keymaster/unlock",
        json={"passphrase": "x"},
        headers=_origin_header(),
    )
    assert r.status_code == 403


def test_keymaster_keys_post_requires_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/keymaster/keys",
        json={"provider": "SHODAN", "label": "l", "api_key": "k", "notes": ""},
        headers=_origin_header(),
    )
    assert r.status_code == 403


def test_keymaster_patch_requires_csrf(logged_in_client):
    r = logged_in_client.patch(
        "/api/keymaster/keys/1",
        json={"label": "l", "notes": ""},
        headers=_origin_header(),
    )
    assert r.status_code == 403


def test_keymaster_delete_requires_csrf(logged_in_client):
    r = logged_in_client.delete("/api/keymaster/keys/1", headers=_origin_header())
    assert r.status_code == 403


def test_keymaster_apply_requires_csrf(logged_in_client):
    r = logged_in_client.post(
        "/api/keymaster/apply",
        json={"key_id": 1},
        headers=_origin_header(),
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Origin guards
# ---------------------------------------------------------------------------

def test_keymaster_keys_post_requires_same_origin(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/keymaster/keys",
        json={"provider": "SHODAN", "label": "l", "api_key": "k", "notes": ""},
        headers={"X-CSRF-Token": csrf, "Origin": "http://attacker.com"},
    )
    assert r.status_code == 403


def test_keymaster_apply_requires_same_origin(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/keymaster/apply",
        json={"key_id": 1},
        headers={"X-CSRF-Token": csrf, "Origin": "http://attacker.com"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/keymaster/status
# ---------------------------------------------------------------------------

def test_keymaster_status_returns_expected_fields(logged_in_client):
    r = logged_in_client.get("/api/keymaster/status")
    assert r.status_code == 200
    data = r.json()
    assert "secure_mode" in data
    assert "passphrase_configured" in data
    assert "is_unlocked" in data
    # secure mode was disabled in fixture so should be unlocked
    assert data["is_unlocked"] is True
    assert data["secure_mode"] is False


# ---------------------------------------------------------------------------
# GET /api/keymaster/keys
# ---------------------------------------------------------------------------

def test_keymaster_list_keys_empty(logged_in_client):
    r = logged_in_client.get("/api/keymaster/keys?provider=SHODAN")
    assert r.status_code == 200
    data = r.json()
    assert data["keys"] == []
    assert "is_unlocked" in data


def test_keymaster_list_keys_normalizes_provider_case(logged_in_client):
    response = logged_in_client.get("/api/keymaster/keys?provider=shodan")

    assert response.status_code == 200
    assert response.json()["keys"] == []


def test_keymaster_list_keys_rejects_provider_with_fixed_error(logged_in_client):
    response = logged_in_client.get("/api/keymaster/keys?provider=invalid")

    assert response.status_code == 422
    assert response.json() == {"error": "unsupported provider"}


def test_keymaster_list_value_error_is_sanitized(
    logged_in_client,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(km_store, "list_keys", _raise_value_error)

    response = logged_in_client.get("/api/keymaster/keys?provider=SHODAN")

    _assert_sanitized(
        response,
        caplog,
        503,
        {"error": "keymaster db unavailable"},
    )
    assert "exception_class=ValueError" in caplog.text


def test_keymaster_list_keys_response_excludes_secrets(logged_in_client):
    csrf = _csrf(logged_in_client)
    _create_key(logged_in_client, csrf, label="sec-test", api_key="myshodan1234")

    r = logged_in_client.get("/api/keymaster/keys?provider=SHODAN")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    row = keys[0]
    # masked display key present
    assert "api_key_masked" in row
    # secret-adjacent fields must be absent
    assert "api_key" not in row
    assert "api_key_normalized" not in row
    assert "key_ciphertext" not in row
    assert "key_fingerprint" not in row
    assert "is_encrypted" not in row
    # masked key should not equal the plaintext
    assert row["api_key_masked"] != "myshodan1234"


# ---------------------------------------------------------------------------
# POST /api/keymaster/keys (create)
# ---------------------------------------------------------------------------

def test_keymaster_create_key(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = _create_key(logged_in_client, csrf, label="my-key", api_key="shodan-key-abc")
    assert r.status_code == 201
    assert "key_id" in r.json()


def test_keymaster_create_duplicate_key_returns_409(logged_in_client):
    csrf = _csrf(logged_in_client)
    _create_key(logged_in_client, csrf, label="dup-key", api_key="dup-api-key-xyz")
    r = _create_key(logged_in_client, csrf, label="dup-key-2", api_key="dup-api-key-xyz")
    assert r.status_code == 409


def test_keymaster_create_invalid_provider_returns_422(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/keymaster/keys",
        json={"provider": "INVALID", "label": "l", "api_key": "k", "notes": ""},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 422


def test_keymaster_create_value_error_is_sanitized(
    logged_in_client,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(km_store, "create_key", _raise_value_error)
    csrf = _csrf(logged_in_client)

    response = _create_key(
        logged_in_client,
        csrf,
        label="value-error",
        api_key="not-returned",
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "internal error"},
    )
    assert "exception_class=ValueError" in caplog.text


# ---------------------------------------------------------------------------
# PATCH /api/keymaster/keys/{key_id}
# ---------------------------------------------------------------------------

def test_keymaster_update_key(logged_in_client):
    csrf = _csrf(logged_in_client)
    cr = _create_key(logged_in_client, csrf, label="orig-label", api_key="key-for-update")
    key_id = cr.json()["key_id"]

    r = logged_in_client.patch(
        f"/api/keymaster/keys/{key_id}",
        json={"label": "new-label", "api_key": "key-for-update", "notes": "updated"},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_keymaster_update_missing_key_returns_404(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.patch(
        "/api/keymaster/keys/9999",
        json={"label": "l", "api_key": "k", "notes": ""},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 404


def test_keymaster_update_blank_api_key_keeps_existing(logged_in_client):
    csrf = _csrf(logged_in_client)
    cr = _create_key(logged_in_client, csrf, label="keep-key", api_key="keep-this-key")
    key_id = cr.json()["key_id"]

    # PATCH with blank api_key — should use existing key
    r = logged_in_client.patch(
        f"/api/keymaster/keys/{key_id}",
        json={"label": "keep-key-renamed", "api_key": "", "notes": ""},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200


def test_keymaster_update_value_error_is_sanitized(
    logged_in_client,
    monkeypatch,
    caplog,
):
    monkeypatch.setattr(km_store, "update_key", _raise_value_error)
    csrf = _csrf(logged_in_client)

    response = logged_in_client.patch(
        "/api/keymaster/keys/1",
        json={"label": "updated", "api_key": "not-returned", "notes": ""},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )

    _assert_sanitized(
        response,
        caplog,
        500,
        {"error": "internal error"},
    )
    assert "exception_class=ValueError" in caplog.text


# ---------------------------------------------------------------------------
# DELETE /api/keymaster/keys/{key_id}
# ---------------------------------------------------------------------------

def test_keymaster_delete_key(logged_in_client):
    csrf = _csrf(logged_in_client)
    cr = _create_key(logged_in_client, csrf, label="to-delete", api_key="delete-me-key")
    key_id = cr.json()["key_id"]

    r = logged_in_client.delete(
        f"/api/keymaster/keys/{key_id}",
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200

    # Confirm gone
    list_r = logged_in_client.get("/api/keymaster/keys?provider=SHODAN")
    keys = list_r.json()["keys"]
    assert not any(k["key_id"] == key_id for k in keys)


def test_keymaster_delete_missing_key_returns_404(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.delete(
        "/api/keymaster/keys/9999",
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/keymaster/apply
# ---------------------------------------------------------------------------

def test_keymaster_apply_writes_config(logged_in_client, main_config_path):
    csrf = _csrf(logged_in_client)
    cr = _create_key(logged_in_client, csrf, label="apply-key", api_key="apply-shodan-xyz")
    key_id = cr.json()["key_id"]

    r = logged_in_client.post(
        "/api/keymaster/apply",
        json={"key_id": key_id},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    data = json.loads(main_config_path.read_text(encoding="utf-8"))
    assert data["shodan"]["api_key"] == "apply-shodan-xyz"


def test_keymaster_apply_missing_key_returns_404(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/keymaster/apply",
        json={"key_id": 9999},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 404


def test_keymaster_apply_no_config(logged_in_client, app, tmp_path):
    # Point app.state.main_config_path to a non-existent file
    app.state.main_config_path = tmp_path / "nonexistent.json"
    csrf = _csrf(logged_in_client)

    # Create a key first so key_id is valid
    cr = _create_key(logged_in_client, csrf, label="no-cfg-key", api_key="nocfg-shodan")
    key_id = cr.json()["key_id"]

    r = logged_in_client.post(
        "/api/keymaster/apply",
        json={"key_id": key_id},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/keymaster/unlock — bad passphrase
# ---------------------------------------------------------------------------

def test_keymaster_unlock_bad_passphrase(logged_in_client, monkeypatch):
    def _raise(*args, **kwargs):
        raise InvalidPassphraseError("invalid passphrase")

    monkeypatch.setattr(km_store, "unlock_session_keys", _raise)

    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/keymaster/unlock",
        json={"passphrase": "wrong"},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 400
    assert "invalid passphrase" in r.json()["error"]


def test_keymaster_unlock_success_sets_session(tmp_path, cfg_no_tls):
    client = _build_secure_logged_in_client(tmp_path, cfg_no_tls)
    csrf = _csrf(client)
    r = client.post(
        "/api/keymaster/unlock",
        json={"passphrase": _KEYMASTER_PASSPHRASE},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    status = client.get("/api/keymaster/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["secure_mode"] is True
    assert payload["is_unlocked"] is True


def test_keymaster_unlock_accepts_trimmed_passphrase(tmp_path, cfg_no_tls):
    client = _build_secure_logged_in_client(tmp_path, cfg_no_tls)
    csrf = _csrf(client)
    r = client.post(
        "/api/keymaster/unlock",
        json={"passphrase": f"  {_KEYMASTER_PASSPHRASE}\n"},
        headers={"X-CSRF-Token": csrf, **_origin_header()},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True
