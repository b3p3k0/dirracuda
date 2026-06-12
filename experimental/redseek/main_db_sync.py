"""Sync helpers for promoting Reddit run rows into the primary DB."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from experimental.redseek import store as reddit_store
from experimental.redseek.mapper import row_to_prefill
from gui.utils.database_access import DatabaseReader
from gui.utils.sidecar_promotion import promote_sidecar_prefills


def sync_targets_to_main_db(
    dedupe_keys: list[str],
    *,
    db_path: Path | str,
) -> dict:
    """
    Promote current-run reddit_targets rows into primary DB host tables.

    Reads rows from ``reddit_targets`` in ``db_path`` whose ``dedupe_key``
    matches one of the supplied keys (typically ``_probe_candidate_keys`` from
    ``IngestResult``), maps each row to a sidecar-promotion prefill, and calls
    ``promote_sidecar_prefills`` to upsert into the primary protocol tables.

    Returns a deterministic summary dict and never raises.
    """
    summary: dict = {
        "selected": 0,
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "cancelled": 0,
    }

    keys = list(dict.fromkeys(k for k in (dedupe_keys or []) if k))
    if not keys:
        return summary

    resolved_db = Path(db_path).expanduser().resolve(strict=False)

    try:
        conn = reddit_store.open_connection(resolved_db)
        try:
            rows = reddit_store.get_targets_by_dedupe_keys(conn, keys)
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
        prefill = row_to_prefill(
            row,
            promotion_source="reddit_run_sync",
            snapshot_source="reddit:run_sync",
        )
        if prefill is None:
            skipped_shape += 1
        else:
            prefills.append(prefill)

    if not prefills:
        summary["processed"] = selected_total
        summary["skipped"] = selected_total
        return summary

    try:
        reader = DatabaseReader(db_path=str(resolved_db), cache_duration=0)
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


__all__ = ["sync_targets_to_main_db"]
