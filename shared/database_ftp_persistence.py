"""
FTP persistence helpers extracted from shared.database.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

class FtpPersistence:
    """
    Write operations for the FTP sidecar tables (ftp_servers, ftp_access).

    Intentionally decoupled from SMBSeekWorkflowDatabase / DatabaseManager so
    that FTP persistence does not depend on SMB infrastructure. Both tables
    must already exist (created by shared.db_migrations.run_migrations).
    """

    # Shared SQL constants used by both per-host and batch methods to prevent drift.
    _UPSERT_SQL = """
        INSERT INTO ftp_servers
            (ip_address, country, country_code, port, anon_accessible,
             banner, shodan_data, last_seen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(ip_address) DO UPDATE SET
            last_seen       = CURRENT_TIMESTAMP,
            scan_count      = ftp_servers.scan_count + 1,
            port            = excluded.port,
            anon_accessible = excluded.anon_accessible,
            banner          = excluded.banner,
            country         = excluded.country,
            country_code    = excluded.country_code,
            shodan_data     = excluded.shodan_data,
            status          = 'active',
            updated_at      = CURRENT_TIMESTAMP
    """

    _ACCESS_SQL = """
        INSERT INTO ftp_access
            (server_id, session_id, accessible, auth_status,
             root_listing_available, root_entry_count,
             error_message, access_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)

    @staticmethod
    def _parse_db_timestamp(value) -> Optional[datetime]:
        """Best-effort parser for SQLite DATETIME values."""
        if not value:
            return None
        try:
            ts = str(value).strip()
            if not ts:
                return None
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            parsed = datetime.fromisoformat(ts)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    def _get_known_ftp_hosts(self, ips: Set[str]) -> Dict[str, Dict]:
        """Fetch known FTP hosts keyed by IP with last_seen metadata."""
        if not ips:
            return {}

        rows_by_ip: Dict[str, Dict] = {}
        batch_size = 500
        ips_list = list(ips)

        try:
            with sqlite3.connect(self.db_path) as conn:
                for i in range(0, len(ips_list), batch_size):
                    batch = ips_list[i:i + batch_size]
                    placeholders = ",".join(["?" for _ in batch])
                    query = f"""
                        SELECT id, ip_address, last_seen
                        FROM ftp_servers
                        WHERE ip_address IN ({placeholders})
                    """
                    cur = conn.execute(query, tuple(batch))
                    for server_id, ip_address, last_seen in cur.fetchall():
                        rows_by_ip[str(ip_address)] = {
                            "id": int(server_id),
                            "last_seen": last_seen,
                        }
        except Exception:
            return {}

        return rows_by_ip

    def _get_latest_ftp_accessibility(self, server_ids: Set[int]) -> Dict[int, int]:
        """Fetch latest ftp_access.accessible per server_id."""
        if not server_ids:
            return {}

        accessibility: Dict[int, int] = {}
        batch_size = 500
        ids_list = list(server_ids)

        try:
            with sqlite3.connect(self.db_path) as conn:
                for i in range(0, len(ids_list), batch_size):
                    batch = ids_list[i:i + batch_size]
                    placeholders = ",".join(["?" for _ in batch])
                    query = f"""
                        SELECT fa.server_id, fa.accessible
                        FROM ftp_access fa
                        WHERE fa.server_id IN ({placeholders})
                          AND fa.id = (
                              SELECT MAX(fa2.id)
                              FROM ftp_access fa2
                              WHERE fa2.server_id = fa.server_id
                          )
                    """
                    cur = conn.execute(query, tuple(batch))
                    for server_id, accessible in cur.fetchall():
                        accessibility[int(server_id)] = int(accessible) if accessible is not None else 0
        except Exception:
            return {}

        return accessibility

    def filter_recent_candidates(
        self,
        candidates: List,
        rescan_after_days: int = 30,
    ) -> Tuple[List, Dict[str, int]]:
        """
        Filter FTP candidates using recent-host policy.

        Policy:
        - New hosts: always scan
        - Known hosts older than cutoff: scan
        - Known hosts within cutoff:
          - latest accessible=1 -> skip (recent success)
          - latest accessible=0 or no access row -> scan (retry recent failure)
        """
        total = len(candidates or [])
        stats = {
            "total": total,
            "new": 0,
            "known": 0,
            "skipped_recent": 0,
            "retried_recent_failures": 0,
            "old_enough": 0,
            "to_scan": 0,
        }

        if not candidates:
            return [], stats

        try:
            days = int(rescan_after_days)
        except Exception:
            days = 30
        if days < 1:
            days = 1

        cutoff = datetime.now() - timedelta(days=days)

        try:
            input_candidates = list(candidates)
            ips = {
                str(getattr(c, "ip", "")).strip()
                for c in input_candidates
                if str(getattr(c, "ip", "")).strip()
            }
            known_hosts = self._get_known_ftp_hosts(ips)
            latest_access = self._get_latest_ftp_accessibility(
                {meta["id"] for meta in known_hosts.values() if "id" in meta}
            )

            filtered: List = []
            for candidate in input_candidates:
                ip = str(getattr(candidate, "ip", "")).strip()
                if not ip:
                    stats["new"] += 1
                    filtered.append(candidate)
                    continue

                host_meta = known_hosts.get(ip)
                if not host_meta:
                    stats["new"] += 1
                    filtered.append(candidate)
                    continue

                stats["known"] += 1

                last_seen_dt = self._parse_db_timestamp(host_meta.get("last_seen"))
                if last_seen_dt is None:
                    # Fail-open on parse ambiguity.
                    filtered.append(candidate)
                    continue

                if last_seen_dt < cutoff:
                    stats["old_enough"] += 1
                    filtered.append(candidate)
                    continue

                latest_accessible = latest_access.get(int(host_meta["id"]), None)
                if latest_accessible == 1:
                    stats["skipped_recent"] += 1
                    continue

                stats["retried_recent_failures"] += 1
                filtered.append(candidate)

            stats["to_scan"] = len(filtered)
            return filtered, stats

        except Exception:
            # Fail-open: never drop scan targets due to filtering errors.
            fail_open = list(candidates)
            stats.update(
                {
                    "new": len(fail_open),
                    "known": 0,
                    "skipped_recent": 0,
                    "retried_recent_failures": 0,
                    "old_enough": 0,
                    "to_scan": len(fail_open),
                }
            )
            return fail_open, stats

    def upsert_ftp_server(
        self,
        ip: str,
        country: str,
        country_code: str,
        port: int,
        anon_accessible: bool,
        banner: str,
        shodan_data: str,
    ) -> int:
        """
        Insert or update the ftp_servers row for ip_address.

        On conflict (same IP re-scanned): increments scan_count, updates
        last_seen and all mutable discovery fields. first_seen is never
        overwritten.

        Returns the authoritative row id, always resolved via SELECT because
        lastrowid is not reliable on the conflict-update code path.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self._UPSERT_SQL, (ip, country, country_code, port,
                                            anon_accessible, banner, shodan_data))
            conn.commit()
            row = conn.execute(
                "SELECT id FROM ftp_servers WHERE ip_address = ?", (ip,)
            ).fetchone()
            return row[0]

    def record_ftp_access(
        self,
        server_id: int,
        session_id: Optional[int],
        accessible: bool,
        auth_status: str,
        root_listing_available: bool,
        root_entry_count: int,
        error_message: str,
        access_details: str,
    ) -> None:
        """
        Insert one ftp_access row for the given server/session.

        One row per session per server is the expected usage pattern; callers
        that need idempotency should check for an existing row first.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self._ACCESS_SQL, (server_id, session_id, accessible,
                                            auth_status, root_listing_available,
                                            root_entry_count, error_message,
                                            access_details))
            conn.commit()

    def persist_discovery_outcomes_batch(self, outcomes: list) -> None:
        """
        Persist stage-1 port-failed hosts in a single transaction.

        Each outcome is an FtpDiscoveryOutcome. Opens one connection, upserts
        each ftp_servers row, resolves server_id, writes an ftp_access row,
        then commits once at the end.
        """
        if not outcomes:
            return
        with sqlite3.connect(self.db_path) as conn:
            for o in outcomes:
                conn.execute(self._UPSERT_SQL, (
                    o.ip, o.country, o.country_code, o.port,
                    False, o.banner, o.shodan_data,
                ))
                row = conn.execute(
                    "SELECT id FROM ftp_servers WHERE ip_address = ?", (o.ip,)
                ).fetchone()
                server_id = row[0]
                conn.execute(self._ACCESS_SQL, (
                    server_id, None, False, o.reason,
                    False, 0, o.error_message,
                    json.dumps({"reason": o.reason, "error": o.error_message}),
                ))
            conn.commit()

    def persist_access_outcomes_batch(self, outcomes: list) -> None:
        """
        Persist stage-2 access results in a single transaction.

        Each outcome is an FtpAccessOutcome. Opens one connection, upserts
        each ftp_servers row, resolves server_id, writes an ftp_access row,
        then commits once at the end.
        """
        if not outcomes:
            return
        with sqlite3.connect(self.db_path) as conn:
            for o in outcomes:
                conn.execute(self._UPSERT_SQL, (
                    o.ip, o.country, o.country_code, o.port,
                    o.accessible, o.banner, o.shodan_data,
                ))
                row = conn.execute(
                    "SELECT id FROM ftp_servers WHERE ip_address = ?", (o.ip,)
                ).fetchone()
                server_id = row[0]
                conn.execute(self._ACCESS_SQL, (
                    server_id, None, o.accessible, o.auth_status,
                    o.root_listing_available, o.root_entry_count,
                    o.error_message, o.access_details,
                ))

                # Keep unified server-list "Shares/Accessible" columns in sync for FTP rows.
                # We source names from access_details.root_entries (if present).
                entries = []
                try:
                    details = json.loads(o.access_details) if o.access_details else {}
                    entries = details.get("root_entries") or []
                    if not isinstance(entries, list):
                        entries = []
                except Exception:
                    entries = []

                entry_names = [
                    str(name).strip()
                    for name in entries
                    if isinstance(name, str) and str(name).strip()
                ]
                dirs_count = int(o.root_entry_count or 0) if (o.accessible and o.root_listing_available) else 0
                dirs_list = ",".join(entry_names)

                conn.execute(
                    """
                    INSERT INTO ftp_probe_cache
                        (server_id, accessible_dirs_count, accessible_dirs_list, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(server_id) DO UPDATE SET
                        accessible_dirs_count = excluded.accessible_dirs_count,
                        accessible_dirs_list  = excluded.accessible_dirs_list,
                        updated_at            = CURRENT_TIMESTAMP
                    """,
                    (server_id, dirs_count, dirs_list),
                )
            conn.commit()

