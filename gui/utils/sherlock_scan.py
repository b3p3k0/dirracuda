"""Reusable Sherlock scan-and-persist helper (C4).

Loads the latest probe snapshot for each target host, runs the pure matcher, and
persists the result. Display-only: never downloads files, reads file contents,
authenticates, or performs any network/probe work - only DB reads/writes plus the
pure `shared.sherlock` matcher. C5's post-probe hook calls the same function with
a single-host target so standalone and post-probe scans cannot drift (R12).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Dict, Iterable, Optional

from shared.sherlock import (
    SherlockSettings,
    match_entries,
    path_entries_from_snapshot,
)


@dataclasses.dataclass
class SherlockScanSummary:
    """Honest per-batch counts. scanned/with_findings/clean count only hosts that
    were both matched and successfully persisted."""

    scanned: int = 0
    with_findings: int = 0
    clean: int = 0
    skipped_no_snapshot: int = 0
    not_persisted: int = 0
    errors: int = 0


def run_sherlock_scan(
    db_reader: Any,
    settings: SherlockSettings,
    targets: Iterable[Dict[str, Any]],
) -> SherlockScanSummary:
    """Scan each target against its latest probe snapshot and persist results.

    targets: iterable of {ip_address, host_type, protocol_server_id, port}.
    Missing snapshots are skipped and counted; a guarded no-op store (returns None)
    is counted under not_persisted and excluded from scanned/with_findings/clean so
    the summary never claims findings that did not persist.
    """
    summary = SherlockScanSummary()

    for target in targets:
        ip_address = target.get("ip_address")
        host_type = target.get("host_type")
        protocol_server_id = target.get("protocol_server_id")
        port = target.get("port")

        try:
            snapshot = db_reader.get_latest_probe_snapshot_with_id_for_host(
                ip_address,
                host_type,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if snapshot is None:
                summary.skipped_no_snapshot += 1
                continue

            snapshot_id, payload = snapshot
            entries = path_entries_from_snapshot(payload)
            result = match_entries(entries, settings)

            result_id: Optional[int] = db_reader.store_sherlock_result(
                ip_address,
                host_type,
                snapshot_id,
                result,
                protocol_server_id=protocol_server_id,
                port=port,
            )
            if result_id is None:
                summary.not_persisted += 1
                continue

            summary.scanned += 1
            if result.hit_count > 0:
                summary.with_findings += 1
            else:
                summary.clean += 1
        except Exception:
            summary.errors += 1

    return summary
