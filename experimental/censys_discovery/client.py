from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from experimental.censys_discovery.models import (
    AUTH_FORBIDDEN,
    AUTH_UNAUTHORIZED,
    CLIENT_ERROR,
    NETWORK_ERROR,
    NOT_FOUND,
    QUERY_INVALID,
    RESPONSE_PARSE_ERROR,
    SERVER_ERROR,
    ApiError,
    ClientResult,
    CreditBalance,
    SearchPage,
    SearchResultItem,
)

_BASE_URL = "https://api.platform.censys.io"

_HTTP_STATUS_REASON_CODES = {
    400: QUERY_INVALID,
    401: AUTH_UNAUTHORIZED,
    403: AUTH_FORBIDDEN,
    404: NOT_FOUND,
    422: QUERY_INVALID,
}

_REQUIRED_FIELDS = [
    "host.ip",
    "host.services.port",
    "host.services.protocol",
    "host.services.transport_protocol",
    "host.services.banner",
    "host.services.scan_time",
]


def _redact(text: str, pat: str) -> str:
    if not pat or not text:
        return text
    return text.replace(pat, "<redacted>")


def _parse_credit_balance(payload: dict) -> CreditBalance:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("missing or non-dict 'result' key")
    if "balance" not in result:
        raise ValueError("missing required 'balance' field")
    return CreditBalance(
        balance=result["balance"],
        resets_at=result.get("resets_at"),
    )


def _parse_hit(hit: dict) -> SearchResultItem:
    host_v1  = hit.get("host_v1") or {}
    resource = host_v1.get("resource") or {}
    ip       = resource.get("ip", "")
    matched  = host_v1.get("matched_services") or []
    all_svcs = resource.get("services") or []

    msvc    = matched[0] if matched else {}
    m_port  = msvc.get("port")
    m_proto = msvc.get("protocol")
    m_trans = msvc.get("transport_protocol")

    # Find full service record from resource.services (has banner/scan_time);
    # matched_services entries carry identity fields only.
    svc = next(
        (s for s in all_svcs
         if s.get("port") == m_port
         and s.get("protocol") == m_proto
         and s.get("transport_protocol") == m_trans),
        msvc,
    )

    return SearchResultItem(
        ip_address=ip,
        port=msvc.get("port", 0),
        protocol=msvc.get("protocol") or msvc.get("service_name", ""),
        transport_protocol=msvc.get("transport_protocol"),
        banner=svc.get("banner"),
        scan_time=svc.get("observed_at") or svc.get("scan_time"),
        source_json=json.dumps(svc) if svc else json.dumps(hit),
    )


def _parse_search_page(payload: dict) -> SearchPage:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("missing or non-dict 'result' key")
    hits      = result.get("hits") or []
    # Drift-tolerant: prefer next_page_token, fallback to links.next
    next_cur  = result.get("next_page_token") or (result.get("links") or {}).get("next")
    # SDK returns float; coerce to int since it represents a count
    raw_total = result.get("total_hits")
    total     = int(raw_total) if raw_total is not None else None
    items     = [_parse_hit(h) for h in hits if isinstance(h, dict)]
    return SearchPage(items=items, next_cursor=next_cur, total_hits=total)


class CensysClient:

    def __init__(self, pat: str, base_url: str = _BASE_URL, timeout: int = 30) -> None:
        self._pat      = pat
        self._base_url = base_url.rstrip("/")
        self._timeout  = timeout

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> ClientResult:
        url  = self._base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req  = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._pat}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in _HTTP_STATUS_REASON_CODES:
                reason_code = _HTTP_STATUS_REASON_CODES[exc.code]
            elif exc.code >= 500:
                reason_code = SERVER_ERROR
            else:
                reason_code = CLIENT_ERROR
            message = _redact(f"HTTP {exc.code}: {exc.reason}", self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(reason_code, exc.code, message))
        except urllib.error.URLError as exc:
            message = _redact(str(exc.reason), self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(NETWORK_ERROR, None, message))
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError) as exc:
            msg = _redact(str(exc), self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(RESPONSE_PARSE_ERROR, None, msg))
        return ClientResult(ok=True, data=payload, error=None)

    def search_query(
        self,
        query: str,
        page_size: int = 100,
        page_token: Optional[str] = None,
        org_id: Optional[str] = None,
        extra_fields: Optional[list] = None,
    ) -> ClientResult:
        merged = list(dict.fromkeys(_REQUIRED_FIELDS + (extra_fields or [])))
        payload = {
            "query":     query,
            "page_size": page_size,
            "fields":    merged,
        }
        if page_token:
            payload["page_token"] = page_token
        url_path = "/v3/global/search/query"
        if org_id:
            url_path += "?" + urllib.parse.urlencode({"organization_id": org_id})
        result = self._request("POST", url_path, body=payload)
        if not result.ok:
            return result
        try:
            page = _parse_search_page(result.data)
            return ClientResult(ok=True, data=page, error=None)
        except Exception as exc:
            msg = _redact(str(exc), self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(RESPONSE_PARSE_ERROR, None, msg))

    def search_aggregate(
        self,
        query: str,
        field: str,
        number_of_buckets: int = 10,
        org_id: Optional[str] = None,
    ) -> ClientResult:
        payload = {
            "query":             query,
            "field":             field,
            "number_of_buckets": number_of_buckets,
        }
        url_path = "/v3/global/search/aggregate"
        if org_id:
            url_path += "?" + urllib.parse.urlencode({"organization_id": org_id})
        return self._request("POST", url_path, body=payload)

    def get_user_credits(self) -> ClientResult:
        result = self._request("GET", "/v3/accounts/users/credits")
        if not result.ok:
            return result
        try:
            return ClientResult(ok=True, data=_parse_credit_balance(result.data), error=None)
        except Exception as exc:
            msg = _redact(str(exc), self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(RESPONSE_PARSE_ERROR, None, msg))

    def get_user_credits_usage(
        self,
        start_date: str,
        end_date: Optional[str] = None,
        granularity: str = "daily",
    ) -> ClientResult:
        params: dict = {"start_date": start_date, "granularity": granularity}
        if end_date:
            params["end_date"] = end_date
        qs = urllib.parse.urlencode(params)
        return self._request("GET", f"/v3/accounts/users/credits/usage?{qs}")

    def get_org_credits(self, org_id: str) -> ClientResult:
        result = self._request("GET", f"/v3/accounts/organizations/{org_id}/credits")
        if not result.ok:
            return result
        try:
            return ClientResult(ok=True, data=_parse_credit_balance(result.data), error=None)
        except Exception as exc:
            msg = _redact(str(exc), self._pat)
            return ClientResult(ok=False, data=None, error=ApiError(RESPONSE_PARSE_ERROR, None, msg))

    def get_org_credits_usage(
        self,
        org_id: str,
        start_date: str,
        end_date: Optional[str] = None,
        granularity: str = "daily",
    ) -> ClientResult:
        params: dict = {"start_date": start_date, "granularity": granularity}
        if end_date:
            params["end_date"] = end_date
        qs = urllib.parse.urlencode(params)
        return self._request("GET", f"/v3/accounts/organizations/{org_id}/credits/usage?{qs}")
