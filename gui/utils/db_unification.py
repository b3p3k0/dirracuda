"""Startup DB-unification helpers for portable analyst intelligence."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from gui.utils.database_access import DatabaseReader
from gui.utils.sidecar_promotion import (
    build_manual_record_payload,
    build_probe_cache_payload,
    build_probe_snapshot_payload,
)
from shared.path_service import get_paths, get_legacy_paths, select_existing_path

_logger = logging.getLogger(__name__)


_PROBE_BACKFILL_DONE_KEY = "db_unification.probe_backfill.completed"
_PROBE_BACKFILL_ERROR_KEY = "db_unification.probe_backfill.last_error"
_PROBE_CLEANUP_PROMPT_KEY = "db_unification.probe_cleanup.prompted"
_SIDECAR_IMPORT_DONE_KEY = "db_unification.sidecar_import.completed"
_SIDECAR_IMPORT_ERROR_KEY = "db_unification.sidecar_import.last_error"
_SIDECAR_PROMPT_STATE_KEY = "db_unification.sidecar_prompt.state"
_SIDECAR_PROMPT_SUMMARY_KEY = "db_unification.sidecar_prompt.last_summary"
_SIDECAR_PROMPT_ERROR_KEY = "db_unification.sidecar_prompt.last_error"


def _legacy_probe_dirs() -> Dict[str, Path]:
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)

    def _pick(*candidates: Path) -> Path:
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue
        return candidates[0]

    return {
        "S": _pick(
            legacy.flat_probe_smb_dir,
            legacy.legacy_home_root / "probes",
        ),
        "F": _pick(
            legacy.flat_probe_ftp_dir,
            legacy.legacy_home_root / "ftp_probes",
        ),
        "H": _pick(
            legacy.flat_probe_http_dir,
            legacy.legacy_home_root / "http_probes",
        ),
    }


def count_legacy_probe_cache_files() -> int:
    total = 0
    for root in _legacy_probe_dirs().values():
        if not root.exists():
            continue
        total += sum(1 for p in root.glob("*.json") if p.is_file())
    return total


def _iter_legacy_snapshot_files() -> Iterable[Tuple[str, Path]]:
    for host_type, root in _legacy_probe_dirs().items():
        if not root.exists():
            continue
        for file_path in sorted(root.glob("*.json")):
            if file_path.is_file():
                yield host_type, file_path


def _extract_snapshot_identity(host_type: str, file_path: Path, snapshot: Dict[str, Any]) -> Tuple[str, Optional[int]]:
    ip_address = str(snapshot.get("ip_address") or "").strip()
    port: Optional[int] = None

    if host_type in ("F", "H"):
        try:
            raw_port = snapshot.get("port")
            if raw_port is not None:
                port = int(raw_port)
        except (TypeError, ValueError):
            port = None

    if not ip_address:
        stem = file_path.stem
        if host_type == "H" and port is None and "_" in stem:
            base, maybe_port = stem.rsplit("_", 1)
            try:
                port = int(maybe_port)
                ip_address = base
            except ValueError:
                ip_address = stem
        else:
            ip_address = stem

    return ip_address, port


def cleanup_legacy_probe_cache_files() -> int:
    deleted = 0
    for _, file_path in _iter_legacy_snapshot_files():
        try:
            file_path.unlink(missing_ok=True)
            deleted += 1
        except Exception:
            continue
    return deleted


def run_probe_snapshot_backfill(reader: DatabaseReader) -> Dict[str, Any]:
    if str(reader.get_migration_state(_PROBE_BACKFILL_DONE_KEY, "0")) == "1":
        return {
            "status": "already_done",
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "prompt_cleanup": False,
            "legacy_files": count_legacy_probe_cache_files(),
        }

    imported = 0
    skipped = 0
    errors = 0
    for host_type, file_path in _iter_legacy_snapshot_files():
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                skipped += 1
                reader.append_migration_report(
                    "probe_snapshot_backfill",
                    "legacy_probe_cache",
                    "invalid_payload",
                    item_key=str(file_path),
                    detail="snapshot payload is not an object",
                )
                continue

            ip_address, port = _extract_snapshot_identity(host_type, file_path, payload)
            if not ip_address:
                skipped += 1
                reader.append_migration_report(
                    "probe_snapshot_backfill",
                    "legacy_probe_cache",
                    "missing_identity",
                    item_key=str(file_path),
                    detail="unable to resolve ip_address",
                )
                continue

            snapshot_id = reader.upsert_probe_snapshot_for_host(
                ip_address,
                host_type,
                payload,
                port=port,
                source="legacy_backfill",
            )
            if snapshot_id is None:
                errors += 1
                reader.append_migration_report(
                    "probe_snapshot_backfill",
                    "legacy_probe_cache",
                    "snapshot_upsert_failed",
                    item_key=f"{host_type}:{ip_address}:{port if port is not None else ''}",
                    detail=str(file_path),
                )
                continue

            reader.set_latest_probe_snapshot_for_host(
                ip_address,
                host_type,
                snapshot_id,
                port=port,
            )
            imported += 1
        except Exception as exc:
            errors += 1
            reader.append_migration_report(
                "probe_snapshot_backfill",
                "legacy_probe_cache",
                "exception",
                item_key=str(file_path),
                detail=str(exc),
            )

    reader.set_migration_state(_PROBE_BACKFILL_DONE_KEY, "1")
    reader.set_migration_state(
        "db_unification.probe_backfill.summary",
        json.dumps({"imported": imported, "skipped": skipped, "errors": errors}),
    )
    reader.set_migration_state(_PROBE_BACKFILL_ERROR_KEY, None)

    prompt_cleanup = (
        str(reader.get_migration_state(_PROBE_CLEANUP_PROMPT_KEY, "0")) != "1"
        and count_legacy_probe_cache_files() > 0
    )
    return {
        "status": "done",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "prompt_cleanup": prompt_cleanup,
        "legacy_files": count_legacy_probe_cache_files(),
    }


def apply_probe_cleanup_choice(reader: DatabaseReader, *, keep_files: bool) -> Dict[str, Any]:
    deleted = 0
    if not keep_files:
        deleted = cleanup_legacy_probe_cache_files()
    reader.set_migration_state(_PROBE_CLEANUP_PROMPT_KEY, "1")
    reader.set_migration_state(
        "db_unification.probe_cleanup.choice",
        "keep" if keep_files else "discard",
    )
    return {"deleted": deleted, "kept": keep_files}


def _resolve_ipv4(host: str) -> Optional[str]:
    host = str(host or "").strip()
    if not host:
        return None
    try:
        ip_obj = ipaddress.ip_address(host)
        if isinstance(ip_obj, ipaddress.IPv4Address):
            return str(ip_obj)
        return None
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        for info in infos:
            addr = info[4][0]
            if addr:
                return str(addr)
    except Exception:
        return None
    return None


def _import_se_dork(reader: DatabaseReader) -> Dict[str, int]:
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    sidecar_path = select_existing_path(
        paths.se_dork_db_file,
        [legacy.flat_sidecar_se_dork_file, legacy.legacy_home_root / "se_dork.db"],
    )
    counts = {"imported": 0, "skipped": 0, "errors": 0}
    if not sidecar_path.is_file():
        return counts

    conn = sqlite3.connect(str(sidecar_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(dork_results)").fetchall()
        }
        optional = {
            "probe_status": "'unprobed' AS probe_status",
            "probe_indicator_matches": "0 AS probe_indicator_matches",
            "probe_preview": "NULL AS probe_preview",
            "probe_checked_at": "NULL AS probe_checked_at",
            "probe_error": "NULL AS probe_error",
            "probe_snapshot_json": "NULL AS probe_snapshot_json",
        }
        select_parts = ["result_id", "url"]
        for column, fallback_sql in optional.items():
            select_parts.append(column if column in columns else fallback_sql)
        rows = conn.execute(
            f"""
            SELECT {", ".join(select_parts)}
            FROM dork_results
            """
        ).fetchall()
    except Exception as exc:
        reader.append_migration_report(
            "sidecar_targeted_import",
            "se_dork",
            "query_failed",
            item_key=str(sidecar_path),
            detail=str(exc),
        )
        conn.close()
        counts["errors"] += 1
        return counts

    for row in rows:
        url = str(row["url"] or "").strip()
        parsed = urlparse(url)
        host = parsed.hostname or ""
        ip_address = _resolve_ipv4(host)
        if not ip_address:
            counts["skipped"] += 1
            reader.append_migration_report(
                "sidecar_targeted_import",
                "se_dork",
                "unresolved_host",
                item_key=f"result_id={row['result_id']}",
                detail=url,
            )
            continue

        scheme = (parsed.scheme or "http").lower()
        if scheme not in {"http", "https"}:
            scheme = "http"
        port = parsed.port or (443 if scheme == "https" else 80)
        prefill = {
            "host_type": "H",
            "host": ip_address,
            "port": port,
            "scheme": scheme,
            "_probe_host_hint": host,
            "_probe_path_hint": parsed.path or "/",
            "_probe_cache": {
                "status": row["probe_status"],
                "indicator_matches": row["probe_indicator_matches"],
                "preview": row["probe_preview"],
                "checked_at": row["probe_checked_at"],
                "error": row["probe_error"],
            },
            "_probe_snapshot": row["probe_snapshot_json"],
            "_probe_snapshot_source": "sidecar:se_dork",
        }

        try:
            payload = build_manual_record_payload(prefill)
            upsert = reader.upsert_manual_server_record(payload)
            probe_cache = build_probe_cache_payload(prefill)
            probe_snapshot = build_probe_snapshot_payload(prefill) if probe_cache is not None else None
            snapshot_id = None
            if probe_snapshot is not None:
                snapshot_id = reader.upsert_probe_snapshot_for_host(
                    payload["ip_address"],
                    "H",
                    probe_snapshot,
                    protocol_server_id=upsert.get("protocol_server_id"),
                    port=payload.get("port"),
                    source="sidecar:se_dork",
                )
            if probe_cache is not None:
                if snapshot_id is not None:
                    probe_cache["latest_snapshot_id"] = snapshot_id
                reader.upsert_probe_cache_for_host(
                    payload["ip_address"],
                    "H",
                    protocol_server_id=upsert.get("protocol_server_id"),
                    port=payload.get("port"),
                    **probe_cache,
                )
            counts["imported"] += 1
        except Exception as exc:
            counts["errors"] += 1
            reader.append_migration_report(
                "sidecar_targeted_import",
                "se_dork",
                "upsert_failed",
                item_key=f"{ip_address}:{port}",
                detail=str(exc),
            )
    conn.close()
    return counts


def _import_redseek(reader: DatabaseReader) -> Dict[str, int]:
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    sidecar_path = select_existing_path(
        paths.reddit_od_db_file,
        [legacy.flat_sidecar_reddit_od_file, legacy.legacy_home_root / "reddit_od.db"],
    )
    counts = {"imported": 0, "skipped": 0, "errors": 0}
    if not sidecar_path.is_file():
        return counts

    conn = sqlite3.connect(str(sidecar_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, host, protocol, notes, target_normalized
            FROM reddit_targets
            """
        ).fetchall()
    except Exception as exc:
        reader.append_migration_report(
            "sidecar_targeted_import",
            "redseek",
            "query_failed",
            item_key=str(sidecar_path),
            detail=str(exc),
        )
        conn.close()
        counts["errors"] += 1
        return counts

    for row in rows:
        protocol = str(row["protocol"] or "").strip().upper()
        if protocol not in {"SMB", "FTP", "HTTP"}:
            counts["skipped"] += 1
            reader.append_migration_report(
                "sidecar_targeted_import",
                "redseek",
                "unsupported_protocol",
                item_key=f"target_id={row['id']}",
                detail=str(protocol),
            )
            continue

        target_norm = str(row["target_normalized"] or "").strip()
        parsed = urlparse(target_norm)
        host_candidate = str(row["host"] or parsed.hostname or "").strip()
        ip_address = _resolve_ipv4(host_candidate)
        if not ip_address:
            counts["skipped"] += 1
            reader.append_migration_report(
                "sidecar_targeted_import",
                "redseek",
                "unresolved_host",
                item_key=f"target_id={row['id']}",
                detail=host_candidate or target_norm,
            )
            continue

        host_type = "S" if protocol == "SMB" else ("F" if protocol == "FTP" else "H")
        payload: Dict[str, Any] = {
            "host_type": host_type,
            "ip_address": ip_address,
        }
        port = None
        if host_type == "F":
            port = parsed.port or 21
            payload["port"] = port
        elif host_type == "H":
            scheme = (parsed.scheme or "http").lower()
            if scheme not in {"http", "https"}:
                scheme = "http"
            port = parsed.port or (443 if scheme == "https" else 80)
            payload["scheme"] = scheme
            payload["port"] = port

        try:
            upsert = reader.upsert_manual_server_record(payload)
            notes = str(row["notes"] or "").strip()
            if notes:
                reader.upsert_user_flags_for_host(
                    ip_address,
                    host_type,
                    notes=notes,
                    protocol_server_id=upsert.get("protocol_server_id"),
                    port=port,
                )
            counts["imported"] += 1
        except Exception as exc:
            counts["errors"] += 1
            reader.append_migration_report(
                "sidecar_targeted_import",
                "redseek",
                "upsert_failed",
                item_key=f"target_id={row['id']}",
                detail=str(exc),
            )

    conn.close()
    return counts


def _has_pending_sidecar_data() -> bool:
    paths = get_paths()
    legacy = get_legacy_paths(paths=paths)
    checks = [
        (
            select_existing_path(
                paths.se_dork_db_file,
                [legacy.flat_sidecar_se_dork_file, legacy.legacy_home_root / "se_dork.db"],
            ),
            "dork_results",
        ),
        (
            select_existing_path(
                paths.reddit_od_db_file,
                [legacy.flat_sidecar_reddit_od_file, legacy.legacy_home_root / "reddit_od.db"],
            ),
            "reddit_targets",
        ),
    ]
    for sidecar_path, table in checks:
        if not sidecar_path.is_file():
            continue
        try:
            conn = sqlite3.connect(f"file:{sidecar_path}?mode=ro", uri=True)
            try:
                row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            finally:
                conn.close()
            if row:
                return True
        except Exception:
            continue
    return False


def detect_pending_sidecar_migration(reader: DatabaseReader) -> bool:
    state = reader.get_migration_state(_SIDECAR_PROMPT_STATE_KEY, "not_started")
    if state in ("deferred", "completed", "in_progress", "failed"):
        return False
    if str(reader.get_migration_state(_SIDECAR_IMPORT_DONE_KEY, "0")) == "1":
        reader.set_migration_state(_SIDECAR_PROMPT_STATE_KEY, "completed")
        return False
    return _has_pending_sidecar_data()


def defer_sidecar_migration(reader: DatabaseReader) -> None:
    reader.set_migration_state(_SIDECAR_PROMPT_STATE_KEY, "deferred")


def execute_sidecar_migration_now(reader: DatabaseReader) -> Dict[str, Any]:
    reader.set_migration_state(_SIDECAR_PROMPT_STATE_KEY, "in_progress")
    try:
        result = run_targeted_sidecar_import(reader)
        reader.set_migration_state(_SIDECAR_PROMPT_STATE_KEY, "completed")
        reader.set_migration_state(_SIDECAR_PROMPT_SUMMARY_KEY, json.dumps(result))
        reader.set_migration_state(_SIDECAR_PROMPT_ERROR_KEY, None)
        return {**result, "status": "completed"}
    except Exception as exc:
        reader.set_migration_state(_SIDECAR_PROMPT_STATE_KEY, "failed")
        reader.set_migration_state(_SIDECAR_PROMPT_ERROR_KEY, str(exc))
        return {"status": "failed", "error": str(exc)}


def run_targeted_sidecar_import(reader: DatabaseReader) -> Dict[str, Any]:
    if str(reader.get_migration_state(_SIDECAR_IMPORT_DONE_KEY, "0")) == "1":
        return {"status": "already_done", "imported": 0, "skipped": 0, "errors": 0}

    se_counts = _import_se_dork(reader)
    reddit_counts = _import_redseek(reader)
    imported = se_counts["imported"] + reddit_counts["imported"]
    skipped = se_counts["skipped"] + reddit_counts["skipped"]
    errors = se_counts["errors"] + reddit_counts["errors"]

    reader.set_migration_state(_SIDECAR_IMPORT_DONE_KEY, "1")
    reader.set_migration_state(
        "db_unification.sidecar_import.summary",
        json.dumps(
            {
                "se_dork": se_counts,
                "redseek": reddit_counts,
                "imported": imported,
                "skipped": skipped,
                "errors": errors,
            }
        ),
    )
    reader.set_migration_state(_SIDECAR_IMPORT_ERROR_KEY, None)

    return {
        "status": "done",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "sources": {
            "se_dork": se_counts,
            "redseek": reddit_counts,
        },
    }


def run_startup_db_unification(db_path: str) -> Dict[str, Any]:
    """Run startup unification tasks and return structured result."""
    reader = DatabaseReader(db_path)
    result: Dict[str, Any] = {
        "success": True,
        "errors": [],
        "probe_backfill": {},
        "prompt_cleanup": False,
        "prompt_sidecar_migration": False,
    }

    try:
        probe_result = run_probe_snapshot_backfill(reader)
        result["probe_backfill"] = probe_result
        result["prompt_cleanup"] = bool(probe_result.get("prompt_cleanup"))
    except Exception as exc:
        result["success"] = False
        result["errors"].append(f"probe backfill failed: {exc}")
        reader.set_migration_state(_PROBE_BACKFILL_ERROR_KEY, str(exc))
        _logger.warning("Probe snapshot backfill failed: %s", exc)

    try:
        result["prompt_sidecar_migration"] = detect_pending_sidecar_migration(reader)
    except Exception as exc:
        _logger.warning("Sidecar migration detection failed: %s", exc)

    return result


__all__ = [
    "apply_probe_cleanup_choice",
    "cleanup_legacy_probe_cache_files",
    "count_legacy_probe_cache_files",
    "detect_pending_sidecar_migration",
    "defer_sidecar_migration",
    "execute_sidecar_migration_now",
    "run_startup_db_unification",
]
