"""Dashboard Shodan balance endpoint tests (C21)."""

import json
from typing import Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from experimental.webui.app import create_app
from experimental.webui.auth import set_password
from experimental.webui.config import TLSConfig, WebUIConfig

_USERNAME = "dashboarduser"
_PASSWORD = "correct-horse-battery-staple"


class _DummyResponse:
    def __init__(
        self,
        status_code: int,
        payload=None,
        json_exc: Optional[Exception] = None,
    ):
        self.status_code = status_code
        self._payload = payload
        self._json_exc = json_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._payload


@pytest.fixture
def creds(tmp_path):
    p = tmp_path / "creds.json"
    set_password(_USERNAME, _PASSWORD, path=p)
    return p


@pytest.fixture
def cfg_no_tls():
    return WebUIConfig(tls=TLSConfig(enabled=False))


@pytest.fixture
def webui_config_path(tmp_path):
    return tmp_path / "webui.json"


@pytest.fixture
def main_config_path(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"shodan": {"api_key": "KEY_A"}}), encoding="utf-8")
    return p


@pytest.fixture
def client(creds, cfg_no_tls, webui_config_path, main_config_path):
    app = create_app(
        cfg=cfg_no_tls,
        creds_path=creds,
        config_path=webui_config_path,
        main_config_path=main_config_path,
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in(client):
    r = client.post("/login", json={"username": _USERNAME, "password": _PASSWORD})
    assert r.status_code == 200
    return client


def test_dashboard_shodan_balance_requires_auth(client):
    r = client.get("/api/dashboard/shodan-balance")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_dashboard_shodan_balance_no_key(logged_in, main_config_path):
    main_config_path.write_text(json.dumps({"shodan": {"api_key": ""}}), encoding="utf-8")
    r = logged_in.get("/api/dashboard/shodan-balance")
    assert r.status_code == 200
    assert r.json() == {"state": "no_key", "cached": False}


def test_dashboard_shodan_balance_success_and_cache(logged_in, monkeypatch):
    calls = []

    def _fake_get(url, *, params, timeout, headers):
        calls.append((url, params, timeout, headers))
        return _DummyResponse(200, {"query_credits": 321})

    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", _fake_get)

    r1 = logged_in.get("/api/dashboard/shodan-balance")
    assert r1.status_code == 200
    assert r1.json() == {"state": "ok", "query_credits": 321, "cached": False}

    r2 = logged_in.get("/api/dashboard/shodan-balance")
    assert r2.status_code == 200
    assert r2.json() == {"state": "ok", "query_credits": 321, "cached": True}
    assert len(calls) == 1


def test_dashboard_shodan_balance_force_bypasses_cache(logged_in, monkeypatch):
    calls = []

    def _fake_get(url, *, params, timeout, headers):
        calls.append(params["key"])
        return _DummyResponse(200, {"query_credits": 400 + len(calls)})

    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", _fake_get)

    r1 = logged_in.get("/api/dashboard/shodan-balance")
    assert r1.status_code == 200
    assert r1.json() == {"state": "ok", "query_credits": 401, "cached": False}

    r2 = logged_in.get("/api/dashboard/shodan-balance?force=true")
    assert r2.status_code == 200
    assert r2.json() == {"state": "ok", "query_credits": 402, "cached": False}
    assert len(calls) == 2


def test_dashboard_shodan_balance_cache_key_change_invalidates(
    logged_in,
    monkeypatch,
    main_config_path,
):
    calls = []

    def _fake_get(url, *, params, timeout, headers):
        calls.append(params["key"])
        if params["key"] == "KEY_A":
            return _DummyResponse(200, {"query_credits": 101})
        return _DummyResponse(200, {"query_credits": 202})

    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", _fake_get)

    r1 = logged_in.get("/api/dashboard/shodan-balance")
    assert r1.status_code == 200
    assert r1.json() == {"state": "ok", "query_credits": 101, "cached": False}

    main_config_path.write_text(json.dumps({"shodan": {"api_key": "KEY_B"}}), encoding="utf-8")
    r2 = logged_in.get("/api/dashboard/shodan-balance")
    assert r2.status_code == 200
    assert r2.json() == {"state": "ok", "query_credits": 202, "cached": False}
    assert calls == ["KEY_A", "KEY_B"]


@pytest.mark.parametrize(
    "fake_get,expected_reason",
    [
        (lambda *_a, **_k: _DummyResponse(401, {}), "auth"),
        (lambda *_a, **_k: _DummyResponse(429, {}), "rate_limited"),
        (lambda *_a, **_k: _DummyResponse(503, {}), "provider"),
        (
            lambda *_a, **_k: _DummyResponse(200, {"query_credits": True}),
            "provider",
        ),
        (
            lambda *_a, **_k: _DummyResponse(200, None, json_exc=ValueError("bad json")),
            "provider",
        ),
    ],
)
def test_dashboard_shodan_balance_reason_mappings(logged_in, monkeypatch, fake_get, expected_reason):
    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", fake_get)
    r = logged_in.get("/api/dashboard/shodan-balance")
    assert r.status_code == 200
    assert r.json() == {"state": "unavailable", "reason": expected_reason, "cached": False}


def test_dashboard_shodan_balance_timeout_mapping(logged_in, monkeypatch):
    def _fake_get(*_a, **_k):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", _fake_get)
    r = logged_in.get("/api/dashboard/shodan-balance")
    assert r.status_code == 200
    assert r.json() == {"state": "unavailable", "reason": "timeout", "cached": False}


def test_dashboard_shodan_balance_network_mapping(logged_in, monkeypatch):
    def _fake_get(*_a, **_k):
        raise httpx.TransportError("network down")

    monkeypatch.setattr("experimental.webui.shodan_balance.httpx.get", _fake_get)
    r = logged_in.get("/api/dashboard/shodan-balance")
    assert r.status_code == 200
    assert r.json() == {"state": "unavailable", "reason": "network", "cached": False}


def test_dashboard_html_does_not_expose_api_key(logged_in):
    r = logged_in.get("/dashboard")
    assert r.status_code == 200
    assert "KEY_A" not in r.text
