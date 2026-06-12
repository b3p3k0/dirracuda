"""WebUI helpers for SearXNG/Reddit experimental feature flows."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable, Optional
from urllib.parse import urlparse

from experimental.redseek import store as red_store
from experimental.se_dork import store as se_store
from gui.utils.database_access import DatabaseReader
from gui.utils.sidecar_probe import (
    SidecarProbeTarget,
    SidecarProbeUnsupported,
    build_indicator_patterns,
    build_probe_target_from_sidecar_row,
    run_sidecar_probe,
    skipped_probe_outcome,
)
from gui.utils.sidecar_promotion import SidecarPromotionError, promote_sidecar_prefill, promote_sidecar_prefills


_REDDIT_ROWS_QUERY = """
SELECT
    t.id,
    t.post_id,
    t.target_normalized,
    t.host,
    t.protocol,
    t.parse_confidence,
    t.notes,
    t.target_raw,
    t.dedupe_key,
    t.created_at,
    t.probe_status,
    t.probe_indicator_matches,
    t.probe_preview,
    t.probe_checked_at,
    t.probe_error,
    t.probe_snapshot_json,
    p.post_author,
    p.post_title,
    p.post_created_utc,
    p.is_nsfw,
    p.source_sort,
    p.last_seen_at
FROM reddit_targets t
LEFT JOIN reddit_posts p ON t.post_id = p.post_id
ORDER BY t.id DESC
"""


def _normalize_start_path(path: str) -> str:
    value = str(path or "/").split("?", 1)[0].split("#", 1)[0].strip() or "/"
    if not value.startswith("/"):
        value = "/" + value.lstrip("/")
    return value


def load_searxng_rows(db_path: Optional[Path]) -> list[dict]:
    se_store.init_db(db_path)
    conn = se_store.open_connection(db_path)
    try:
        # Retain OPEN_INDEX rows only for consistent browser behavior.
        se_store.delete_non_open_results(conn, run_id=None)
        conn.commit()
        return se_store.get_all_results(conn)
    finally:
        conn.close()


def load_reddit_rows(db_path: Optional[Path]) -> list[dict]:
    red_store.init_db(db_path)
    conn = red_store.open_connection(db_path)
    try:
        cur = conn.execute(_REDDIT_ROWS_QUERY)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def probe_searxng_rows(
    db_path: Optional[Path],
    result_ids: list[int],
    *,
    config_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    selected_ids = {int(i) for i in result_ids if int(i) > 0}
    rows = [row for row in load_searxng_rows(db_path) if int(row.get("result_id") or 0) in selected_ids]
    total = len(rows)
    summary = {
        "selected": len(selected_ids),
        "processed": 0,
        "updated": 0,
        "clean": 0,
        "issue": 0,
        "unprobed": 0,
        "failed": 0,
        "cancelled": 0,
    }

    patterns = build_indicator_patterns(config_path)
    stop_event = cancel_event or threading.Event()

    conn = se_store.open_connection(db_path)
    try:
        for idx, row in enumerate(rows, start=1):
            if stop_event.is_set():
                break

            result_id = int(row.get("result_id") or 0)
            url = str(row.get("url") or "").strip()
            outcome = None

            try:
                parsed = urlparse(url)
                scheme = str(parsed.scheme or "").lower()
                host = str(parsed.hostname or "").strip()
                if scheme not in {"http", "https"} or not host:
                    raise SidecarProbeUnsupported("unsupported scheme or missing hostname")
                try:
                    port = parsed.port or (443 if scheme == "https" else 80)
                except ValueError:
                    raise SidecarProbeUnsupported("invalid URL port")

                target = SidecarProbeTarget(
                    host_type="H",
                    host=host,
                    port=int(port),
                    scheme=scheme,
                    request_host=host,
                    start_path=_normalize_start_path(parsed.path or "/"),
                )
                outcome = run_sidecar_probe(
                    target,
                    config_path=config_path,
                    indicator_patterns=patterns,
                    cancel_event=stop_event,
                )
            except SidecarProbeUnsupported as exc:
                outcome = skipped_probe_outcome(str(exc))
            except Exception as exc:
                outcome = skipped_probe_outcome(str(exc))

            se_store.update_result_probe(
                conn,
                result_id=result_id,
                probe_status=outcome.probe_status,
                probe_indicator_matches=outcome.probe_indicator_matches,
                probe_preview=outcome.probe_preview,
                probe_checked_at=outcome.probe_checked_at,
                probe_error=outcome.probe_error,
                probe_snapshot_payload=getattr(outcome, "probe_snapshot_payload", None),
            )
            summary["processed"] += 1
            summary["updated"] += 1
            if outcome.probe_status == "clean":
                summary["clean"] += 1
            elif outcome.probe_status == "issue":
                summary["issue"] += 1
            else:
                summary["unprobed"] += 1

            if callable(progress_callback):
                progress_callback(summary["processed"], total, f"Probed {idx}/{total}")

        conn.commit()
    finally:
        conn.close()

    summary["cancelled"] = max(0, total - summary["processed"])
    return summary


def probe_reddit_rows(
    db_path: Optional[Path],
    target_ids: list[int],
    *,
    config_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    selected_ids = {int(i) for i in target_ids if int(i) > 0}
    rows = [row for row in load_reddit_rows(db_path) if int(row.get("id") or 0) in selected_ids]
    total = len(rows)
    summary = {
        "selected": len(selected_ids),
        "processed": 0,
        "updated": 0,
        "clean": 0,
        "issue": 0,
        "unprobed": 0,
        "failed": 0,
        "cancelled": 0,
    }

    patterns = build_indicator_patterns(config_path)
    stop_event = cancel_event or threading.Event()

    conn = red_store.open_connection(db_path)
    try:
        for idx, row in enumerate(rows, start=1):
            if stop_event.is_set():
                break

            target_id = int(row.get("id") or 0)
            try:
                target = build_probe_target_from_sidecar_row(row)
                outcome = run_sidecar_probe(
                    target,
                    config_path=config_path,
                    indicator_patterns=patterns,
                    cancel_event=stop_event,
                )
            except SidecarProbeUnsupported as exc:
                outcome = skipped_probe_outcome(str(exc))
            except Exception as exc:
                outcome = skipped_probe_outcome(str(exc))

            red_store.update_target_probe(
                conn,
                target_id=target_id,
                probe_status=outcome.probe_status,
                probe_indicator_matches=outcome.probe_indicator_matches,
                probe_preview=outcome.probe_preview,
                probe_checked_at=outcome.probe_checked_at,
                probe_error=outcome.probe_error,
                probe_snapshot_payload=getattr(outcome, "probe_snapshot_payload", None),
            )
            summary["processed"] += 1
            summary["updated"] += 1
            if outcome.probe_status == "clean":
                summary["clean"] += 1
            elif outcome.probe_status == "issue":
                summary["issue"] += 1
            else:
                summary["unprobed"] += 1

            if callable(progress_callback):
                progress_callback(summary["processed"], total, f"Probed {idx}/{total}")

        conn.commit()
    finally:
        conn.close()

    summary["cancelled"] = max(0, total - summary["processed"])
    return summary


def _searxng_prefill_from_row(row: dict) -> Optional[dict]:
    url = str(row.get("url") or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
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
        "_promotion_source": "searxng_webui",
        "_probe_cache": {
            "status": row.get("probe_status"),
            "indicator_matches": row.get("probe_indicator_matches"),
            "preview": row.get("probe_preview"),
            "checked_at": row.get("probe_checked_at"),
            "error": row.get("probe_error"),
        },
        "_probe_snapshot_source": "sidecar:se_dork",
    }
    snapshot = row.get("probe_snapshot_json")
    if isinstance(snapshot, str) and snapshot.strip().startswith("{"):
        try:
            import json

            prefill["_probe_snapshot"] = json.loads(snapshot)
        except Exception:
            pass
    return prefill


def _reddit_prefill_from_row(row: dict) -> Optional[dict]:
    protocol = str(row.get("protocol") or "").strip().lower()
    if protocol in {"http", "https"}:
        host_type = "H"
        scheme = protocol
    elif protocol == "ftp":
        host_type = "F"
        scheme = None
    elif protocol == "smb":
        host_type = "S"
        scheme = None
    else:
        return None

    target_norm = str(row.get("target_normalized") or "").strip()
    host = str(row.get("host") or "").strip()
    if not host:
        return None

    port = None
    if target_norm:
        try:
            parsed = urlparse(target_norm if "://" in target_norm else f"{protocol or 'http'}://{target_norm}")
            port = parsed.port
        except Exception:
            port = None

    prefill = {
        "host_type": host_type,
        "host": host,
        "port": port,
        "scheme": scheme,
        "_promotion_source": "reddit_webui",
        "_probe_cache": {
            "status": row.get("probe_status"),
            "indicator_matches": row.get("probe_indicator_matches"),
            "preview": row.get("probe_preview"),
            "checked_at": row.get("probe_checked_at"),
            "error": row.get("probe_error"),
        },
        "_probe_snapshot_source": "sidecar:reddit",
    }

    if host_type == "H":
        parse_target = target_norm if "://" in target_norm else f"{scheme or 'http'}://{target_norm}"
        try:
            parsed = urlparse(parse_target)
            prefill["_probe_host_hint"] = str(parsed.hostname or host)
            prefill["_probe_path_hint"] = _normalize_start_path(parsed.path or "/")
        except Exception:
            prefill["_probe_host_hint"] = host
            prefill["_probe_path_hint"] = "/"

    snapshot = row.get("probe_snapshot_json")
    if isinstance(snapshot, str) and snapshot.strip().startswith("{"):
        try:
            import json

            prefill["_probe_snapshot"] = json.loads(snapshot)
        except Exception:
            pass
    return prefill


def _build_db_reader(main_db_path: Path) -> DatabaseReader:
    return DatabaseReader(db_path=str(Path(main_db_path).resolve(strict=False)), cache_duration=0)


def promote_searxng_rows(
    main_db_path: Path,
    rows: list[dict],
    result_ids: list[int],
) -> dict:
    selected_ids = {int(i) for i in result_ids if int(i) > 0}
    selected_rows = [row for row in rows if int(row.get("result_id") or 0) in selected_ids]
    prefills = [prefill for prefill in (_searxng_prefill_from_row(row) for row in selected_rows) if prefill]

    reader = _build_db_reader(main_db_path)
    if len(prefills) == 1:
        try:
            promote_sidecar_prefill(reader, prefills[0])
            return {
                "selected": len(selected_ids),
                "processed": 1,
                "inserted": 0,
                "updated": 1,
                "skipped": len(selected_ids) - 1,
                "failed": 0,
                "cancelled": 0,
            }
        except SidecarPromotionError as exc:
            return {
                "selected": len(selected_ids),
                "processed": 1,
                "inserted": 0,
                "updated": 0,
                "skipped": 1,
                "failed": 0,
                "cancelled": 0,
                "error": str(exc),
            }
    return promote_sidecar_prefills(reader, prefills)


def promote_reddit_rows(
    main_db_path: Path,
    rows: list[dict],
    target_ids: list[int],
) -> dict:
    selected_ids = {int(i) for i in target_ids if int(i) > 0}
    selected_rows = [row for row in rows if int(row.get("id") or 0) in selected_ids]
    prefills = [prefill for prefill in (_reddit_prefill_from_row(row) for row in selected_rows) if prefill]

    reader = _build_db_reader(main_db_path)
    if len(prefills) == 1:
        try:
            promote_sidecar_prefill(reader, prefills[0])
            return {
                "selected": len(selected_ids),
                "processed": 1,
                "inserted": 0,
                "updated": 1,
                "skipped": len(selected_ids) - 1,
                "failed": 0,
                "cancelled": 0,
            }
        except SidecarPromotionError as exc:
            return {
                "selected": len(selected_ids),
                "processed": 1,
                "inserted": 0,
                "updated": 0,
                "skipped": 1,
                "failed": 0,
                "cancelled": 0,
                "error": str(exc),
            }
    return promote_sidecar_prefills(reader, prefills)
