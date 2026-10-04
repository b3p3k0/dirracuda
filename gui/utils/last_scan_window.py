"""Persist the most recent desktop provider-queue scan window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Optional

from shared import path_service


def record_last_scan_window(
    start_utc: datetime,
    end_utc: datetime,
    db_path,
    *,
    providers: list[str],
    cancelled: bool,
    paths=None,
) -> bool:
    temp_path = None
    try:
        start = (
            start_utc.replace(tzinfo=timezone.utc)
            if start_utc.tzinfo is None
            else start_utc.astimezone(timezone.utc)
        )
        end = (
            end_utc.replace(tzinfo=timezone.utc)
            if end_utc.tzinfo is None
            else end_utc.astimezone(timezone.utc)
        )
        start = start.replace(microsecond=0)
        if end.microsecond:
            end = end.replace(microsecond=0) + timedelta(seconds=1)
        payload = {
            "version": 1,
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "db_path": str(Path(db_path).resolve()),
            "providers": list(providers),
            "cancelled": bool(cancelled),
        }
        state_dir = (paths or path_service.get_paths()).state_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=state_dir, delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle)
        os.replace(temp_path, state_dir / "last_scan_window.json")
        return True
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not record last scan window: %s", exc)
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


def load_last_scan_window(db_path, *, paths=None) -> Optional[dict]:
    try:
        if not db_path:
            return None
        window_path = (paths or path_service.get_paths()).state_dir / "last_scan_window.json"
        with window_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if payload.get("db_path") != str(Path(db_path).resolve()):
            return None
        if payload.get("version") != 1:
            return None
        providers = payload.get("providers")
        cancelled = payload.get("cancelled")
        if not isinstance(providers, list) or not all(
            isinstance(provider, str) for provider in providers
        ):
            return None
        if not isinstance(cancelled, bool):
            return None
        start = datetime.strptime(payload["start"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        end = datetime.strptime(payload["end"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        if end < start:
            return None
        return {
            "start": start,
            "end": end,
            "providers": providers,
            "cancelled": cancelled,
        }
    except Exception:
        return None
