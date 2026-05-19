"""Server-side Shodan query credit lookup for Web UI dashboard."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from shared.path_service import get_paths

_API_INFO_URL = "https://api.shodan.io/api-info"
_REQUEST_TIMEOUT_SECONDS = 5.0
_CACHE_TTL_SECONDS = 150

_REASON_AUTH = "auth"
_REASON_TIMEOUT = "timeout"
_REASON_NETWORK = "network"
_REASON_RATE_LIMITED = "rate_limited"
_REASON_PROVIDER = "provider"
_REASON_UNKNOWN = "unknown"

_ALLOWED_REASONS = frozenset(
    {
        _REASON_AUTH,
        _REASON_TIMEOUT,
        _REASON_NETWORK,
        _REASON_RATE_LIMITED,
        _REASON_PROVIDER,
        _REASON_UNKNOWN,
    }
)


def _canonical_main_config_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    return get_paths().config_file


def _read_shodan_api_key(path: Path) -> str:
    """Read shodan.api_key from main config, returning empty string when unavailable."""
    if not path.exists():
        return ""
    try:
        config_data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(config_data, dict):
        return ""
    shodan_cfg = config_data.get("shodan")
    if not isinstance(shodan_cfg, dict):
        return ""
    return str(shodan_cfg.get("api_key") or "").strip()


def _fingerprint_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _coerce_query_credits(payload: dict) -> Optional[int]:
    credits = payload.get("query_credits")
    if isinstance(credits, bool):
        return None
    if isinstance(credits, int):
        return credits
    if isinstance(credits, float):
        if not math.isfinite(credits):
            return None
        return int(credits)
    if isinstance(credits, str):
        text = credits.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        return int(value)
    return None


def _sanitize_reason(reason: str) -> str:
    if reason in _ALLOWED_REASONS:
        return reason
    return _REASON_UNKNOWN


def _fetch_live_query_credits(api_key: str, timeout_seconds: float) -> dict:
    try:
        response = httpx.get(
            _API_INFO_URL,
            params={"key": api_key},
            timeout=timeout_seconds,
            headers={"Accept": "application/json"},
        )
    except httpx.TimeoutException:
        return {"state": "unavailable", "reason": _REASON_TIMEOUT}
    except httpx.TransportError:
        return {"state": "unavailable", "reason": _REASON_NETWORK}
    except Exception:
        return {"state": "unavailable", "reason": _REASON_UNKNOWN}

    if response.status_code in (401, 403):
        return {"state": "unavailable", "reason": _REASON_AUTH}
    if response.status_code == 429:
        return {"state": "unavailable", "reason": _REASON_RATE_LIMITED}
    if response.status_code >= 400:
        return {"state": "unavailable", "reason": _REASON_PROVIDER}

    try:
        payload = response.json()
    except Exception:
        return {"state": "unavailable", "reason": _REASON_PROVIDER}
    if not isinstance(payload, dict):
        return {"state": "unavailable", "reason": _REASON_PROVIDER}

    query_credits = _coerce_query_credits(payload)
    if query_credits is None:
        return {"state": "unavailable", "reason": _REASON_PROVIDER}

    return {"state": "ok", "query_credits": query_credits}


class ShodanBalanceService:
    """Resolve Shodan query credits with short-lived in-memory caching."""

    def __init__(
        self,
        *,
        main_config_path: Optional[Path] = None,
        cache_ttl_seconds: int = _CACHE_TTL_SECONDS,
        request_timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._main_config_path = _canonical_main_config_path(main_config_path)
        self._cache_ttl_seconds = max(1, int(cache_ttl_seconds))
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._lock = threading.Lock()
        self._cache_by_fingerprint: dict[str, dict] = {}

    def get_balance(self, *, force: bool = False) -> dict:
        api_key = _read_shodan_api_key(self._main_config_path)
        if not api_key:
            return {"state": "no_key", "cached": False}

        fingerprint = _fingerprint_key(api_key)
        now = time.time()

        with self._lock:
            self._cache_by_fingerprint = {
                k: v
                for k, v in self._cache_by_fingerprint.items()
                if v.get("expires_at", 0.0) > now and k == fingerprint
            }
            if not force:
                cached = self._cache_by_fingerprint.get(fingerprint)
                if cached is not None:
                    return {**cached["payload"], "cached": True}

        payload = _fetch_live_query_credits(api_key, self._request_timeout_seconds)
        if payload.get("state") == "unavailable":
            payload["reason"] = _sanitize_reason(str(payload.get("reason") or _REASON_UNKNOWN))

        with self._lock:
            self._cache_by_fingerprint[fingerprint] = {
                "expires_at": now + self._cache_ttl_seconds,
                "payload": dict(payload),
            }

        return {**payload, "cached": False}
