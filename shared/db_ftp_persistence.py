import json
import sqlite3
from typing import Optional


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
