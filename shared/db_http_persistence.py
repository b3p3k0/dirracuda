import sqlite3


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
        ON CONFLICT(ip_address) DO UPDATE SET
            last_seen    = CURRENT_TIMESTAMP,
            scan_count   = http_servers.scan_count + 1,
            port         = excluded.port,
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
            The authoritative server_id for this IP.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                self._UPSERT_SQL,
                (ip, country, country_code, port, scheme, banner, title, shodan_data),
            )
            row = conn.execute(
                "SELECT id FROM http_servers WHERE ip_address = ?", (ip,)
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
                    "SELECT id FROM http_servers WHERE ip_address = ?", (o.ip,)
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
                    "SELECT id FROM http_servers WHERE ip_address = ?", (o.ip,)
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
