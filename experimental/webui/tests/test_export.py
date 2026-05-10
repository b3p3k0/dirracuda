"""Route integration tests for Web UI export endpoints (C5)."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

import experimental.webui.db as _db
from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "exportuser"
_PASSWORD = "correct-horse-battery-staple"


# ---------------------------------------------------------------------------
# Fixtures — credentials and config
# ---------------------------------------------------------------------------


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


# ---------------------------------------------------------------------------
# Fixtures — databases and export dir
# ---------------------------------------------------------------------------


@pytest.fixture
def real_db(tmp_path):
    """A real SQLite file with smb_servers and one row."""
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE
        );
        INSERT INTO smb_servers (ip_address) VALUES ('192.168.1.1');
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def export_dir(tmp_path):
    return tmp_path / "exports"


# ---------------------------------------------------------------------------
# Fixtures — clients
# ---------------------------------------------------------------------------


@pytest.fixture
def client(creds, cfg_no_tls, real_db, export_dir, monkeypatch):
    monkeypatch.setattr(_db, "_EXPORT_DIR", export_dir)
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=real_db)
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


# ---------------------------------------------------------------------------
# Auth / CSRF protection
# ---------------------------------------------------------------------------


def test_export_requires_auth(client):
    r = client.post("/api/export")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_export_missing_csrf(logged_in_client):
    r = logged_in_client.post("/api/export")
    assert r.status_code == 403


def test_export_bad_origin(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post(
        "/api/export",
        headers={"X-CSRF-Token": csrf, "origin": "http://attacker.com"},
    )
    assert r.status_code == 403


def test_download_requires_auth(client, export_dir):
    export_dir.mkdir(parents=True, exist_ok=True)
    fake = export_dir / "dirracuda_export_20250101_120000_abcd1234.db"
    fake.write_bytes(b"")
    r = client.get(f"/api/export/{fake.name}")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# ---------------------------------------------------------------------------
# Export artifact
# ---------------------------------------------------------------------------


def test_export_creates_file_in_controlled_dir(logged_in_client, export_dir):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    data = r.json()
    assert "filename" in data
    filename = data["filename"]
    assert "/" not in filename
    assert "\\" not in filename
    assert filename.startswith("dirracuda_export_")
    assert filename.endswith(".db")
    artifact = export_dir / filename
    assert artifact.exists()
    # Verify it is a real SQLite DB containing smb_servers
    conn = sqlite3.connect(str(artifact))
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    assert "smb_servers" in tables


def test_export_filename_always_matches_pattern(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    filename = r.json()["filename"]
    assert _db._EXPORT_FILENAME_RE.fullmatch(filename), (
        f"filename {filename!r} does not match _EXPORT_FILENAME_RE"
    )


def test_response_does_not_include_full_path(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    data = r.json()
    assert "path" not in data
    assert "/" not in data["filename"]


def test_rapid_exports_unique_filenames(logged_in_client):
    csrf = _csrf(logged_in_client)
    r1 = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    r2 = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["filename"] != r2.json()["filename"]


# ---------------------------------------------------------------------------
# No-create regression — mode=rw must not create an empty source DB
# ---------------------------------------------------------------------------


def test_export_missing_source_db_returns_500(
    creds, cfg_no_tls, export_dir, tmp_path, monkeypatch
):
    missing_db = tmp_path / "missing.db"
    assert not missing_db.exists()

    monkeypatch.setattr(_db, "_EXPORT_DIR", export_dir)
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=missing_db)
    c = TestClient(app, follow_redirects=False)
    c.post("/login", json={"username": _USERNAME, "password": _PASSWORD})

    dash = c.get("/dashboard")
    m = re.search(r'name="csrf-token" content="([^"]+)"', dash.text)
    csrf = m.group(1)

    r = c.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 500

    # No artifact should have been created
    if export_dir.exists():
        artifacts = list(export_dir.glob("*.db"))
        assert artifacts == [], f"Unexpected export artifact: {artifacts}"

    # Source DB must still not exist (SQLite did not create it)
    assert not missing_db.exists()


# ---------------------------------------------------------------------------
# Download validation
# ---------------------------------------------------------------------------


def test_download_arbitrary_name_rejected(logged_in_client):
    r = logged_in_client.get("/api/export/foo.db")
    assert r.status_code == 400


def test_download_traversal_slash_rejected(logged_in_client):
    # %2F may be decoded by Starlette router (→ 404) or reach handler (→ 400)
    r = logged_in_client.get("/api/export/..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)


def test_download_valid_filename(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    filename = r.json()["filename"]
    r2 = logged_in_client.get(f"/api/export/{filename}")
    assert r2.status_code == 200
    assert "application/octet-stream" in r2.headers.get("content-type", "")


def test_download_nonexistent_file_returns_404(logged_in_client):
    r = logged_in_client.get(
        "/api/export/dirracuda_export_19000101_000000_deadbeef.db"
    )
    assert r.status_code == 404


def test_download_no_full_path_exposure(logged_in_client):
    csrf = _csrf(logged_in_client)
    r = logged_in_client.post("/api/export", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    data = r.json()
    for key, val in data.items():
        if isinstance(val, str):
            assert not val.startswith("/"), (
                f"Response field {key!r} looks like an absolute path: {val!r}"
            )
