"""Focused regression tests for the red-team remediation pass."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig
from experimental.webui.request_security import (
    DEFAULT_BODY_LIMIT,
    HEADER_BYTES_LIMIT,
    HEADER_COUNT_LIMIT,
    LOGIN_BODY_LIMIT,
    REQUEST_TARGET_LIMIT,
)

_USERNAME = "security-user"
_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def client(tmp_path):
    creds = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=creds)
    cfg = WebUIConfig(tls=TLSConfig(enabled=False))
    app = create_app(
        cfg=cfg,
        creds_path=creds,
        db_path=tmp_path / "main.db",
        rl_db_path=tmp_path / "rate.db",
        config_path=tmp_path / "webui.json",
        main_config_path=tmp_path / "main.json",
    )
    return TestClient(app, follow_redirects=False)


def test_login_content_length_limit_precedes_validation(client):
    response = client.post(
        "/login",
        content=b"x" * (LOGIN_BODY_LIMIT + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["connection"] == "close"


def test_default_body_limit_precedes_auth(client):
    response = client.post(
        "/config",
        content=b"x" * (DEFAULT_BODY_LIMIT + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_request_target_limit_returns_414(client):
    response = client.get("/?" + ("q" * REQUEST_TARGET_LIMIT))

    assert response.status_code == 414


def test_header_count_limit_returns_431(client):
    headers = {f"X-Test-{index}": "x" for index in range(HEADER_COUNT_LIMIT + 1)}

    response = client.get("/health", headers=headers)

    assert response.status_code == 431


def test_header_byte_limit_returns_431(client):
    response = client.get(
        "/health",
        headers={"X-Oversized": "x" * HEADER_BYTES_LIMIT},
    )

    assert response.status_code == 431


def test_chunked_login_body_limit_returns_413(tmp_path):
    creds = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=creds)
    app = create_app(
        cfg=WebUIConfig(tls=TLSConfig(enabled=False)),
        creds_path=creds,
        db_path=tmp_path / "main.db",
        rl_db_path=tmp_path / "rate.db",
        main_config_path=tmp_path / "main.json",
    )
    chunks = iter(
        [
            {
                "type": "http.request",
                "body": b"x" * 3000,
                "more_body": True,
            },
            {
                "type": "http.request",
                "body": b"x" * 3000,
                "more_body": False,
            },
        ]
    )
    sent = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/login",
        "raw_path": b"/login",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 2600),
        "state": {},
    }

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "192.168.1.251:2600", "[::1]:2600", "localhost:2600"],
)
def test_ip_literals_and_localhost_are_accepted(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 200


def test_configured_dns_host_is_accepted(tmp_path):
    cfg = WebUIConfig(
        trusted_hosts=["scanbox.lan"],
        tls=TLSConfig(enabled=False),
    )
    app = create_app(
        cfg=cfg,
        db_path=tmp_path / "main.db",
        rl_db_path=tmp_path / "rate.db",
        main_config_path=tmp_path / "main.json",
    )
    client = TestClient(app, follow_redirects=False)

    assert client.get("/health", headers={"Host": "SCANBOX.LAN:2600"}).status_code == 200


@pytest.mark.parametrize(
    "host",
    ["evil.example", "*.example.com", "user@example.com", "example.com/path", ""],
)
def test_untrusted_or_malformed_hosts_are_rejected(client, host):
    assert client.get("/health", headers={"Host": host}).status_code == 400


def test_forwarded_host_is_ignored(client):
    response = client.get(
        "/health",
        headers={
            "Host": "127.0.0.1",
            "X-Forwarded-Host": "evil.example",
            "Forwarded": "host=evil.example",
        },
    )

    assert response.status_code == 200


def test_trusted_host_and_origin_must_still_match(tmp_path):
    creds = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=creds)
    app = create_app(
        cfg=WebUIConfig(
            trusted_hosts=["scanbox.lan"],
            tls=TLSConfig(enabled=False),
        ),
        creds_path=creds,
        db_path=tmp_path / "main.db",
        rl_db_path=tmp_path / "rate.db",
        main_config_path=tmp_path / "main.json",
    )
    client = TestClient(app, follow_redirects=False)
    payload = {"username": _USERNAME, "password": "wrong-password"}

    same = client.post(
        "/login",
        json=payload,
        headers={"Host": "scanbox.lan", "Origin": "http://scanbox.lan"},
    )
    mismatched = client.post(
        "/login",
        json=payload,
        headers={"Host": "scanbox.lan", "Origin": "http://evil.example"},
    )

    assert same.status_code == 401
    assert mismatched.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "", "password": "x"},
        {"username": "x" * 129, "password": "x"},
        {"username": " user", "password": "x"},
        {"username": "user\nname", "password": "x"},
        {"username": "x", "password": ""},
        {"username": "x", "password": "x" * 1025},
        {"username": "x", "password": "x", "extra": True},
    ],
)
def test_login_model_rejects_invalid_or_extra_fields(client, payload):
    assert client.post("/login", json=payload).status_code == 422


def test_attacker_username_is_not_logged_or_stored(tmp_path, caplog):
    creds = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=creds)
    rate_db = tmp_path / "rate.db"
    app = create_app(
        cfg=WebUIConfig(tls=TLSConfig(enabled=False)),
        creds_path=creds,
        db_path=tmp_path / "main.db",
        rl_db_path=rate_db,
        main_config_path=tmp_path / "main.json",
    )
    client = TestClient(app, follow_redirects=False)
    attacker = "A" * 128

    with caplog.at_level(logging.WARNING, logger="experimental.webui.app"):
        response = client.post(
            "/login",
            json={"username": attacker, "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert attacker not in caplog.text
    assert "account_id=" in caplog.text
    with sqlite3.connect(rate_db) as conn:
        values = conn.execute(
            "SELECT key, account_hash, ip_hash FROM auth_attempts"
        ).fetchall()
    assert values
    assert attacker not in repr(values)
