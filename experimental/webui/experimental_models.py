"""Pydantic request models for WebUI experimental feature endpoints."""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from experimental.redseek.models import DEFAULT_MAX_POSTS, MAX_POSTS
from experimental.se_dork.models import DEFAULT_MAX_RESULTS, MAX_RESULTS


def _validate_http_url(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    parsed = urlparse(text)
    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"{field_name} must start with http:// or https://")
    if not str(parsed.netloc or "").strip():
        raise ValueError(f"{field_name} must include a host")
    return text.rstrip("/")


class SearxngPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    instance_url: str

    @field_validator("instance_url")
    @classmethod
    def _validate_instance_url(cls, value: str) -> str:
        return _validate_http_url(value, field_name="instance_url")


class SearxngRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    instance_url: str
    query: str
    max_results: int = DEFAULT_MAX_RESULTS
    bulk_probe_enabled: bool = False
    probe_worker_count: Optional[int] = None

    @field_validator("instance_url")
    @classmethod
    def _validate_instance_url(cls, value: str) -> str:
        return _validate_http_url(value, field_name="instance_url")

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("query is required")
        if len(text) > 500:
            raise ValueError("query must not exceed 500 characters")
        return text

    @field_validator("max_results")
    @classmethod
    def _validate_max_results(cls, value: int) -> int:
        if value < 1 or value > MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_RESULTS}")
        return value

    @field_validator("probe_worker_count")
    @classmethod
    def _validate_probe_worker_count(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value < 1 or value > 8:
            raise ValueError("probe_worker_count must be between 1 and 8")
        return value


class SearxngActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    result_ids: list[int]

    @field_validator("result_ids")
    @classmethod
    def _validate_result_ids(cls, value: list[int]) -> list[int]:
        cleaned = [int(item) for item in value if int(item) > 0]
        deduped = list(dict.fromkeys(cleaned))
        if len(deduped) < 1:
            raise ValueError("result_ids must contain at least one ID")
        if len(deduped) > 200:
            raise ValueError("result_ids must not exceed 200 IDs")
        return deduped


class RedditRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: str = "feed"
    sort: Literal["new", "top"] = "new"
    top_window: Literal["hour", "day", "week", "month", "year", "all"] = "week"
    max_posts: int = DEFAULT_MAX_POSTS
    max_pages: int = 3
    parse_body: bool = True
    include_nsfw: bool = False
    replace_cache: bool = False
    query: str = ""
    username: str = ""
    bulk_probe_enabled: bool = False
    probe_worker_count: Optional[int] = None

    @field_validator("max_posts")
    @classmethod
    def _validate_max_posts(cls, value: int) -> int:
        if value < 1 or value > MAX_POSTS:
            raise ValueError(f"max_posts must be between 1 and {MAX_POSTS}")
        return value

    @field_validator("max_pages")
    @classmethod
    def _validate_max_pages(cls, value: int) -> int:
        if value < 1 or value > 3:
            raise ValueError("max_pages must be between 1 and 3")
        return value

    @field_validator("query")
    @classmethod
    def _normalize_query(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        text = str(value or "").strip()
        if text.lower().startswith("u/"):
            text = text[2:].strip()
        return text

    @field_validator("probe_worker_count")
    @classmethod
    def _validate_probe_worker_count(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        if value < 1 or value > 8:
            raise ValueError("probe_worker_count must be between 1 and 8")
        return value

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        text = str(value or "feed").strip() or "feed"
        if text == "user":
            raise ValueError("user mode is unavailable; anonymous Reddit RSS supports feed/search only")
        if text not in {"feed", "search"}:
            raise ValueError("mode must be feed or search")
        return text

    @model_validator(mode="after")
    def _validate_mode_specific_fields(self):
        if self.mode == "search" and not self.query:
            raise ValueError("query is required for search mode")
        return self


class RedditActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_ids: list[int]

    @field_validator("target_ids")
    @classmethod
    def _validate_target_ids(cls, value: list[int]) -> list[int]:
        cleaned = [int(item) for item in value if int(item) > 0]
        deduped = list(dict.fromkeys(cleaned))
        if len(deduped) < 1:
            raise ValueError("target_ids must contain at least one ID")
        if len(deduped) > 200:
            raise ValueError("target_ids must not exceed 200 IDs")
        return deduped


class DorkbookEntryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol: Literal["SMB", "FTP", "HTTP"]
    nickname: str = Field(default="", max_length=200)
    query: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default="", max_length=2000)


class DorkbookPrefillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entry_id: int


class KeymasterUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    passphrase: str

    @field_validator("passphrase")
    @classmethod
    def _validate_passphrase(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("passphrase is required")
        if len(text.encode("utf-8")) > 1024:
            raise ValueError("passphrase exceeds 1024 bytes")
        return value


class KeymasterKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: str = "SHODAN"
    label: str
    api_key: str
    notes: str = ""

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        v = str(value or "").strip().upper()
        if v not in {"SHODAN"}:
            raise ValueError(f"unsupported provider: {value!r}")
        return v

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("label is required")
        if len(text) > 128:
            raise ValueError("label must not exceed 128 characters")
        return value

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("api_key is required")
        if len(text) > 256:
            raise ValueError("api_key must not exceed 256 characters")
        return value

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, value: str) -> str:
        text = str(value or "")
        if len(text) > 512:
            raise ValueError("notes must not exceed 512 characters")
        return text


class KeymasterKeyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str
    api_key: str = ""
    notes: str = ""

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("label is required")
        if len(text) > 128:
            raise ValueError("label must not exceed 128 characters")
        return value

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: str) -> str:
        text = str(value or "")
        if len(text) > 256:
            raise ValueError("api_key must not exceed 256 characters")
        return text

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, value: str) -> str:
        text = str(value or "")
        if len(text) > 512:
            raise ValueError("notes must not exceed 512 characters")
        return text


class KeymasterApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key_id: int

    @field_validator("key_id")
    @classmethod
    def _validate_key_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("key_id must be a positive integer")
        return value
