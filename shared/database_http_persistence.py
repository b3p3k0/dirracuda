"""
HTTP persistence helpers extracted from shared.database.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from shared.db_migrations import run_migrations

class HttpPersistence:
    """
    Write operations for the HTTP sidecar tables (http_servers, http_access).

    Intentionally decoupled from SMBSeekWorkflowDatabase / DatabaseManager so
    that HTTP persistence does not depend on SMB infrastructure. All tables
    must already exist (created by shared.db_migrations.run_migrations).
    """

    _UPSERT_SQL = """
        INSERT INTO http_servers
            (ip_address, country, country_code, port, scheme,
             banner, title, shodan_data, last_seen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(ip_address, port) DO UPDATE SET
            last_seen    = CURRENT_TIMESTAMP,
            scan_count   = http_servers.scan_count + 1,
            scheme       = excluded.scheme,
            banner       = excluded.banner,
            title        = excluded.title,
            country      = excluded.country,
            country_code = excluded.country_code,
            shodan_data  = excluded.shodan_data,
            status       = 'active',
            updated_at   = CURRENT_TIMESTAMP
    """

    _ACCESS_SQL = """
        INSERT INTO http_access
            (server_id, session_id, accessible, status_code, is_index_page,
             dir_count, file_count, tls_verified, error_message, access_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        # Enforce one-time/ongoing idempotent schema upgrades before HTTP writes.
        # This prevents runtime ON CONFLICT errors when users open older DB files.
        run_migrations(self.db_path)

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

    def _get_known_http_endpoints(self, ips: Set[str]) -> Dict[Tuple[str, int], Dict]:
        """Fetch known HTTP endpoints keyed by (ip, port) with last_seen metadata."""
        if not ips:
            return {}

        rows_by_endpoint: Dict[Tuple[str, int], Dict] = {}
        batch_size = 500
        ips_list = list(ips)

        try:
            with sqlite3.connect(self.db_path) as conn:
                for i in range(0, len(ips_list), batch_size):
                    batch = ips_list[i:i + batch_size]
                    placeholders = ",".join(["?" for _ in batch])
                    query = f"""
                        SELECT id, ip_address, port, last_seen
                        FROM http_servers
                        WHERE ip_address IN ({placeholders})
                    """
                    cur = conn.execute(query, tuple(batch))
                    for server_id, ip_address, port, last_seen in cur.fetchall():
                        try:
                            endpoint = (str(ip_address), int(port))
                        except Exception:
                            continue
                        rows_by_endpoint[endpoint] = {
                            "id": int(server_id),
                            "last_seen": last_seen,
                        }
        except Exception:
            return {}

        return rows_by_endpoint

    def _get_latest_http_accessibility(self, server_ids: Set[int]) -> Dict[int, int]:
        """Fetch latest http_access.accessible per server_id."""
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
                        SELECT ha.server_id, ha.accessible
                        FROM http_access ha
                        WHERE ha.server_id IN ({placeholders})
                          AND ha.id = (
                              SELECT MAX(ha2.id)
                              FROM http_access ha2
                              WHERE ha2.server_id = ha.server_id
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
        Filter HTTP candidates using recent-endpoint policy keyed by (ip, port).

        Policy:
        - New endpoints: always scan
        - Known endpoints older than cutoff: scan
        - Known endpoints within cutoff:
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
            known_endpoints = self._get_known_http_endpoints(ips)
            latest_access = self._get_latest_http_accessibility(
                {meta["id"] for meta in known_endpoints.values() if "id" in meta}
            )

            filtered: List = []
            for candidate in input_candidates:
                ip = str(getattr(candidate, "ip", "")).strip()
                try:
                    port = int(getattr(candidate, "port"))
                except Exception:
                    stats["new"] += 1
                    filtered.append(candidate)
                    continue

                endpoint = (ip, port)
                endpoint_meta = known_endpoints.get(endpoint)
                if not endpoint_meta:
                    stats["new"] += 1
                    filtered.append(candidate)
                    continue

                stats["known"] += 1

                last_seen_dt = self._parse_db_timestamp(endpoint_meta.get("last_seen"))
                if last_seen_dt is None:
                    # Fail-open on parse ambiguity.
                    filtered.append(candidate)
                    continue

                if last_seen_dt < cutoff:
                    stats["old_enough"] += 1
                    filtered.append(candidate)
                    continue

                latest_accessible = latest_access.get(int(endpoint_meta["id"]), None)
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

    def upsert_http_server(
        self,
        ip: str,
        country: str,
        country_code: str,
        port: int,
        scheme: str,
        banner: str,
        title: str,
        shodan_data: str,
    ) -> int:
        """
        Insert or update an http_servers row.

        Returns:
            The authoritative server_id for this endpoint (ip+port).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                self._UPSERT_SQL,
                (ip, country, country_code, port, scheme, banner, title, shodan_data),
            )
            row = conn.execute(
                "SELECT id FROM http_servers WHERE ip_address = ? AND port = ?",
                (ip, int(port)),
            ).fetchone()
            conn.commit()
            return row[0]

    def record_http_access(
        self,
        server_id: int,
        session_id,
        accessible: bool,
        status_code,
        is_index_page: bool,
        dir_count: int,
        file_count: int,
        tls_verified: bool,
        error_message,
        access_details,
    ) -> None:
        """Insert a single http_access row."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                self._ACCESS_SQL,
                (
                    server_id,
                    session_id,
                    1 if accessible else 0,
                    status_code,
                    1 if is_index_page else 0,
                    dir_count or 0,
                    file_count or 0,
                    1 if tls_verified else 0,
                    error_message,
                    access_details,
                ),
            )
            conn.commit()

    def persist_discovery_outcomes_batch(self, outcomes) -> None:
        """
        Batch persist stage-1 (reachability check) failed outcomes.

        For each HttpDiscoveryOutcome:
          - upsert http_servers row
          - insert http_access row (accessible=0, all counts 0)
          - no http_probe_cache entry for stage-1 failures
        """
        import json
        if not outcomes:
            return
        with sqlite3.connect(self.db_path) as conn:
            for o in outcomes:
                conn.execute(
                    self._UPSERT_SQL,
                    (
                        o.ip,
                        o.country,
                        o.country_code,
                        o.port,
                        o.scheme,
                        o.banner,
                        o.title,
                        o.shodan_data if isinstance(o.shodan_data, str)
                        else json.dumps(o.shodan_data),
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM http_servers WHERE ip_address = ? AND port = ?",
                    (o.ip, int(o.port)),
                ).fetchone()
                if row:
                    server_id = row[0]
                    conn.execute(
                        self._ACCESS_SQL,
                        (
                            server_id,
                            None,           # session_id
                            0,              # accessible
                            0,              # status_code
                            0,              # is_index_page
                            0,              # dir_count
                            0,              # file_count
                            0,              # tls_verified
                            o.error_message,
                            json.dumps({"reason": o.reason}),
                        ),
                    )
            conn.commit()

    def persist_access_outcomes_batch(self, outcomes) -> None:
        """
        Batch persist stage-2 access results with http_probe_cache sync.

        For each HttpAccessOutcome:
          - upsert http_servers row
          - insert http_access row (all fields)
          - upsert http_probe_cache row (written for ALL outcomes, including failures)

        The http_probe_cache is written for ALL outcomes so that the GUI's
        "Shares > 0" filter (which uses accessible_dirs_count + accessible_files_count)
        correctly shows 0-count hosts as filtered rather than missing.
        """
        import json
        if not outcomes:
            return

        _PROBE_CACHE_SQL = """
            INSERT INTO http_probe_cache
                (server_id, accessible_dirs_count, accessible_files_count,
                 accessible_dirs_list, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(server_id) DO UPDATE SET
                accessible_dirs_count  = excluded.accessible_dirs_count,
                accessible_files_count = excluded.accessible_files_count,
                accessible_dirs_list   = excluded.accessible_dirs_list,
                updated_at             = CURRENT_TIMESTAMP
        """

        with sqlite3.connect(self.db_path) as conn:
            for o in outcomes:
                conn.execute(
                    self._UPSERT_SQL,
                    (
                        o.ip,
                        o.country,
                        o.country_code,
                        o.port,
                        o.scheme,
                        o.banner,
                        o.title,
                        o.shodan_data if isinstance(o.shodan_data, str)
                        else json.dumps(o.shodan_data),
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM http_servers WHERE ip_address = ? AND port = ?",
                    (o.ip, int(o.port)),
                ).fetchone()
                if not row:
                    continue
                server_id = row[0]

                conn.execute(
                    self._ACCESS_SQL,
                    (
                        server_id,
                        None,                           # session_id
                        1 if o.accessible else 0,
                        o.status_code,
                        1 if o.is_index_page else 0,
                        o.dir_count,
                        o.file_count,
                        1 if o.tls_verified else 0,
                        o.error_message,
                        o.access_details if isinstance(o.access_details, str)
                        else json.dumps(o.access_details),
                    ),
                )

                # Derive accessible_dirs_list from access_details subdirs.
                try:
                    details = json.loads(o.access_details) if isinstance(o.access_details, str) else {}
                    subdirs = details.get("subdirs", [])
                    dir_names = [
                        s["path"].strip("/")
                        for s in subdirs
                        if isinstance(s, dict) and s.get("path")
                    ]
                    dirs_list = ",".join(dir_names)
                except Exception:
                    dirs_list = ""

                conn.execute(
                    _PROBE_CACHE_SQL,
                    (server_id, o.dir_count, o.file_count, dirs_list),
                )

            conn.commit()

