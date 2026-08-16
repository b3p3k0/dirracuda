"""Portable structured references for persisted extraction summaries."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ExtractSummarySource(str, Enum):
    PRIMARY_DB = "primary_db"
    FALLBACK_JSON = "fallback_json"


@dataclass(frozen=True, slots=True)
class ExtractSummaryReference:
    """Exactly one durable extraction-summary location, safe for GUI handoff."""

    db_row_id: int | None
    fallback_log_path: Path | None = field(repr=False)
    source: ExtractSummarySource

    def __post_init__(self) -> None:
        if type(self.source) is not ExtractSummarySource:
            raise ValueError("extract summary source is invalid")
        if self.source is ExtractSummarySource.PRIMARY_DB:
            valid = (
                type(self.db_row_id) is int
                and self.db_row_id > 0
                and self.fallback_log_path is None
            )
        else:
            raw = (
                os.fspath(self.fallback_log_path)
                if isinstance(self.fallback_log_path, Path)
                else ""
            )
            parts = tuple(raw.split("/")[1:]) if raw.startswith("/") else ()
            valid = (
                self.db_row_id is None
                and isinstance(self.fallback_log_path, Path)
                and self.fallback_log_path.is_absolute()
                and parts
                and all(part not in {"", ".", ".."} for part in parts)
                and "\\" not in raw
                and "\x00" not in raw
            )
        if not valid:
            raise ValueError("extract summary reference is inconsistent")

    @property
    def display_token(self) -> str:
        if self.source is ExtractSummarySource.PRIMARY_DB:
            return f"extract summary row {self.db_row_id}"
        assert self.fallback_log_path is not None
        return f"extract summary file {self.fallback_log_path.name}"


__all__ = ["ExtractSummaryReference", "ExtractSummarySource"]
