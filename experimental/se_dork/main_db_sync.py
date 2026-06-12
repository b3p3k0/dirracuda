"""Sync helpers for promoting SearXNG run rows into the primary DB."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from experimental.se_dork import store as se_store
from gui.utils.database_access import DatabaseReader
from gui.utils.sidecar_promotion import promote_sidecar_prefills


def _normalize_start_path(path: str) -> str:
    value = str(path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not value.startswith("/"):
        value = "/" + value.lstrip("/")
    return value


def _row_to_prefill(row: dict) -> Optional[dict]:
    url = str(row.get("url") or "").strip()
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return None

    host = str(parsed.hostname or "").strip()
    if not host:
        return None

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None

    prefill = {
        "host_type": "H",
        "host": host,
        "port": int(port),
        "scheme": scheme,
        "_probe_host_hint": host,
        "_probe_path_hint": _normalize_start_path(parsed.path or "/"),
        "_promotion_source": "searxng_run_sync",
        "_probe_cache": {
            "status": row.get("probe_status"),
            "indicator_matches": row.get("probe_indicator_matches"),
            "preview": row.get("probe_preview"),
            "checked_at": row.get("probe_checked_at"),
            "error": row.get("probe_error"),
        },
        "_probe_snapshot_source": "searxng:run_sync",
    }

    snapshot_text = row.get("probe_snapshot_json")
    if isinstance(snapshot_text, str) and snapshot_text.strip().startswith("{"):
        try:
            prefill["_probe_snapshot"] = json.loads(snapshot_text)
        except Exception:
            pass
    return prefill


def sync_run_to_main_db(
    run_id: Optional[int],
    *,
    db_path: Path | str,
) -> dict:
    """
    Promote retained rows for one run_id into primary DB host tables.

    Returns a deterministic summary and never raises.
    """
    summary = {
        "selected": 0,
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "cancelled": 0,
    }

    try:
        normalized_run_id = int(run_id or 0)
    except (TypeError, ValueError):
        normalized_run_id = 0
    if normalized_run_id <= 0:
        return summary

    resolved_db_path = Path(db_path).expanduser().resolve(strict=False)

    try:
        conn = se_store.open_connection(resolved_db_path)
        try:
            rows = se_store.get_full_results_for_run(conn, normalized_run_id)
        finally:
            conn.close()
    except Exception as exc:
        summary["error"] = str(exc)
        return summary

    selected_total = len(rows)
    summary["selected"] = selected_total
    if selected_total == 0:
        return summary

    prefills: list[dict] = []
    skipped_shape = 0
    for row in rows:
        prefill = _row_to_prefill(row)
        if prefill is None:
            skipped_shape += 1
            continue
        prefills.append(prefill)

    if not prefills:
        summary["processed"] = selected_total
        summary["skipped"] = selected_total
        return summary

    try:
        reader = DatabaseReader(db_path=str(resolved_db_path), cache_duration=0)
        promote_summary = promote_sidecar_prefills(reader, prefills)
    except Exception as exc:
        summary["processed"] = selected_total
        summary["failed"] = len(prefills)
        summary["skipped"] = skipped_shape
        summary["error"] = str(exc)
        return summary

    processed_prefills = int(promote_summary.get("processed", 0) or 0)
    inserted = int(promote_summary.get("inserted", 0) or 0)
    updated = int(promote_summary.get("updated", 0) or 0)
    skipped = int(promote_summary.get("skipped", 0) or 0)
    failed = int(promote_summary.get("failed", 0) or 0)
    cancelled = int(promote_summary.get("cancelled", 0) or 0)

    summary["inserted"] = inserted
    summary["updated"] = updated
    summary["skipped"] = skipped + skipped_shape
    summary["failed"] = failed
    summary["cancelled"] = cancelled
    summary["processed"] = min(
        selected_total,
        processed_prefills + skipped_shape,
    )
    if summary["processed"] < selected_total:
        summary["cancelled"] = max(
            summary["cancelled"],
            selected_total - summary["processed"],
        )
    return summary


__all__ = ["sync_run_to_main_db"]
