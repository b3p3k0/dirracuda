"""Protocol-aware post-scan probe helpers for the Web UI queue runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Optional

from gui.utils.database_access import DatabaseReader
from gui.utils.probe_cache_dispatch import dispatch_probe_run
from gui.utils import probe_patterns
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot

_PROTOCOL_TO_HOST_TYPE = {"smb": "S", "ftp": "F", "http": "H"}
_DEFAULT_DB_PATH = Path.home() / ".dirracuda" / "data" / "dirracuda.db"


@dataclass
class ProbeStageResult:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: bool = False
    warnings: list[str] = field(default_factory=list)


def _resolve_db_path(config_path: Path, db_path: Optional[Path]) -> Path:
    if db_path is not None:
        return Path(db_path).expanduser()
    try:
        from shared.config import load_config as load_core_config

        cfg = load_core_config(str(config_path))
        return Path(cfg.get_database_path()).expanduser()
    except Exception:
        return _DEFAULT_DB_PATH


def _cohort_targets(
    db_reader: DatabaseReader,
    host_type: str,
    scan_start_iso: Optional[str],
    scan_end_iso: Optional[str],
) -> list[dict]:
    cohort_ids = db_reader.get_protocol_scan_cohort_server_ids(
        host_type, scan_start_iso, scan_end_iso
    )
    if not cohort_ids:
        return []
    rows, _ = db_reader.get_protocol_server_list(
        limit=None,
        offset=0,
        country_filter=None,
        recent_scan_only=False,
    )
    targets: list[dict] = []
    for row in rows:
        if str(row.get("host_type") or "").upper() != host_type:
            continue
        try:
            server_id = int(row.get("protocol_server_id"))
        except (TypeError, ValueError):
            continue
        if server_id not in cohort_ids:
            continue
        if host_type == "F":
            anon_accessible = row.get("anon_accessible")
            if isinstance(anon_accessible, str):
                allowed = anon_accessible.strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "y",
                    "on",
                }
            else:
                allowed = bool(anon_accessible)
            if not allowed:
                continue
        targets.append(row)
    return targets


def _run_target_probe(
    *,
    db_reader: DatabaseReader,
    target: dict,
    host_type: str,
    indicator_patterns: list[tuple[str, object]],
    cancel_event: threading.Event,
    max_dirs: int,
    max_files: int,
    timeout_seconds: int,
    max_depth: int,
) -> None:
    ip_address = str(target.get("ip_address") or "").strip()
    if not ip_address:
        raise ValueError("missing ip_address")

    kwargs = {
        "max_directories": max_dirs,
        "max_files": max_files,
        "timeout_seconds": timeout_seconds,
        "cancel_event": cancel_event,
        "max_depth": max_depth,
        "db_reader": db_reader,
    }

    protocol_server_id = target.get("protocol_server_id")
    port = None
    if target.get("port") is not None:
        try:
            port = int(target["port"])
        except (TypeError, ValueError):
            port = None

    if host_type == "S":
        raw_shares = (
            target.get("accessible_shares_list")
            or target.get("accessible_shares")
            or ""
        )
        shares = [s.strip() for s in str(raw_shares).split(",") if s.strip()]
        auth_method = str(target.get("auth_method") or "")
        kwargs.update(
            {
                "shares": shares,
                "username": "" if "anonymous" in auth_method.lower() else "guest",
                "password": "",
                "enable_rce": False,
                "allow_empty": True,
            }
        )
    elif host_type == "F":
        kwargs["port"] = port if port is not None else 21
    else:
        kwargs["port"] = port if port is not None else 80
        kwargs["scheme"] = target.get("scheme")
        kwargs["protocol_server_id"] = protocol_server_id

    snapshot = dispatch_probe_run(ip_address, host_type, **kwargs)
    analysis = probe_patterns.attach_indicator_analysis(snapshot, indicator_patterns)
    status = "issue" if bool(analysis.get("is_suspicious")) else "clean"
    matches = len(analysis.get("matches", []))

    if host_type == "S":
        snapshot_id = db_reader.upsert_probe_snapshot_for_host(ip_address, "S", snapshot)
        db_reader.upsert_probe_cache_for_host(
            ip_address,
            "S",
            status=status,
            indicator_matches=matches,
            snapshot_path=None,
            latest_snapshot_id=snapshot_id,
        )
        return

    if host_type == "F":
        snapshot_id = db_reader.upsert_probe_snapshot_for_host(
            ip_address,
            "F",
            snapshot,
            protocol_server_id=protocol_server_id,
            port=port if port is not None else 21,
        )
        summary = summarize_probe_snapshot(snapshot)
        display_entries = summary["display_entries"]
        db_reader.upsert_probe_cache_for_host(
            ip_address,
            "F",
            status=status,
            indicator_matches=matches,
            snapshot_path=None,
            latest_snapshot_id=snapshot_id,
            accessible_dirs_count=len(display_entries),
            accessible_dirs_list=",".join(display_entries),
            protocol_server_id=protocol_server_id,
            port=port if port is not None else 21,
        )
        return

    snapshot_id = db_reader.upsert_probe_snapshot_for_host(
        ip_address,
        "H",
        snapshot,
        protocol_server_id=protocol_server_id,
        port=port if port is not None else 80,
    )
    summary = summarize_probe_snapshot(snapshot)
    db_reader.upsert_probe_cache_for_host(
        ip_address,
        "H",
        status=status,
        indicator_matches=matches,
        snapshot_path=None,
        latest_snapshot_id=snapshot_id,
        accessible_dirs_count=len(summary["directory_names"]),
        accessible_dirs_list=",".join(summary["display_entries"]),
        accessible_files_count=int(summary["total_file_count"]),
        protocol_server_id=protocol_server_id,
        port=port if port is not None else 80,
    )


def run_post_scan_probe(
    *,
    protocol: str,
    config_path: Path,
    scan_start_iso: Optional[str],
    scan_end_iso: Optional[str],
    cancel_event: threading.Event,
    db_path: Optional[Path] = None,
    max_dirs: int = 3,
    max_files: int = 5,
    timeout_seconds: int = 10,
    max_depth: int = 1,
) -> ProbeStageResult:
    """Run protocol-aware post-scan probe pass for one scan cohort."""
    result = ProbeStageResult()
    host_type = _PROTOCOL_TO_HOST_TYPE.get(protocol)
    if host_type is None:
        result.failed = 1
        result.warnings.append(f"unsupported protocol {protocol!r}")
        return result

    resolved_db_path = _resolve_db_path(config_path, db_path)
    if not resolved_db_path.exists():
        result.warnings.append(f"database not found: {resolved_db_path}")
        return result

    try:
        db_reader = DatabaseReader(str(resolved_db_path))
        indicators = probe_patterns.load_ransomware_indicators(str(config_path))
        indicator_patterns = probe_patterns.compile_indicator_patterns(indicators)
        targets = _cohort_targets(db_reader, host_type, scan_start_iso, scan_end_iso)
    except Exception as exc:
        result.failed = 1
        result.warnings.append(f"probe stage setup failed: {exc}")
        return result
    if not targets:
        return result

    for target in targets:
        if cancel_event.is_set():
            result.cancelled = True
            break
        result.attempted += 1
        try:
            _run_target_probe(
                db_reader=db_reader,
                target=target,
                host_type=host_type,
                indicator_patterns=indicator_patterns,
                cancel_event=cancel_event,
                max_dirs=max_dirs,
                max_files=max_files,
                timeout_seconds=timeout_seconds,
                max_depth=max_depth,
            )
        except Exception as exc:
            if cancel_event.is_set():
                result.cancelled = True
                break
            result.failed += 1
            ip_address = str(target.get("ip_address") or "unknown")
            result.warnings.append(f"{ip_address}: {exc}")
            continue
        result.succeeded += 1

    return result
