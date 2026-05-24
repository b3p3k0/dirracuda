"""Pydantic request models for WebUI experimental feature endpoints."""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    max_results: int = 50
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
        if value < 1 or value > 500:
            raise ValueError("max_results must be between 1 and 500")
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

    mode: Literal["feed", "search", "user"] = "feed"
    sort: Literal["new", "top"] = "new"
    top_window: Literal["hour", "day", "week", "month", "year", "all"] = "week"
    max_posts: int = 100
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
        if value < 1 or value > 200:
            raise ValueError("max_posts must be between 1 and 200")
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

    @model_validator(mode="after")
    def _validate_mode_specific_fields(self):
        if self.mode == "search" and not self.query:
            raise ValueError("query is required for search mode")
        if self.mode == "user" and not self.username:
            raise ValueError("username is required for user mode")
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
