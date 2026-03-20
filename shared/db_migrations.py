"""
Lightweight, idempotent database migrations for SMBSeek.

Currently installs:
- share_credentials: stores per-share credentials discovered via Pry (or future sources).
- host_probe_cache: caches probe status including RCE analysis results.
- ftp_servers: FTP server registry (sidecar, coexists with smb_servers per IP).
- ftp_access: per-session FTP access summary.
- ftp_user_flags: per-FTP-server user flags (favorite/avoid/notes), parallel to host_user_flags.
- ftp_probe_cache: per-FTP-server probe cache (status/indicators/extracted/rce), parallel to host_probe_cache.
- http_servers: HTTP server registry (sidecar, host_type='H', coexists with smb_servers/ftp_servers per IP).
- http_access: per-session HTTP access summary (status_code, dir_count, file_count, tls_verified).
- http_user_flags: per-HTTP-server user flags (favorite/avoid/notes), parallel to host_user_flags.
- http_probe_cache: per-HTTP-server probe cache (status/indicators/dirs/files/extracted/rce), parallel to host_probe_cache.
- v_host_protocols: view resolving has_smb / has_ftp / has_http / protocol_presence per IP.
- Timestamp canonicalization: normalizes existing T-format timestamps in smb_servers/ftp_servers/http_servers
  first_seen/last_seen to canonical YYYY-MM-DD HH:MM:SS format.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional


def run_migrations(db_path: str) -> None:
    """
    Run required migrations against the SQLite database.

    Args:
        db_path: Path to the SQLite database file.
    """
    if not db_path:
        return

    path_obj = Path(db_path)
    # Ensure parent directory exists to avoid sqlite 'unable to open database file'
    path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(path_obj))
        cur = conn.cursor()
        _ensure_core_smb_tables(cur)
        _backfill_smb_servers_from_legacy_servers(cur)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS share_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                share_name TEXT NOT NULL,
                username TEXT,
                password TEXT,
                source TEXT DEFAULT 'pry',
                session_id INTEGER,
                last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS host_user_flags (
                server_id INTEGER PRIMARY KEY,
                favorite BOOLEAN DEFAULT 0,
                avoid BOOLEAN DEFAULT 0,
                notes TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS host_probe_cache (
                server_id INTEGER PRIMARY KEY,
                status TEXT DEFAULT 'unprobed',
                last_probe_at DATETIME,
                indicator_matches INTEGER DEFAULT 0,
                indicator_samples TEXT,
                snapshot_path TEXT,
                extracted INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_share_credentials_server_share_source
            ON share_credentials (server_id, share_name, source)
            """
        )

        # One-time migration: import favorites/avoids/probe status from legacy settings if present
        _import_legacy_settings(cur)

        # Migration: add extracted flag if missing
        cur.execute("PRAGMA table_info(host_probe_cache)")
        columns = [row[1] for row in cur.fetchall()]
        if "extracted" not in columns:
            cur.execute("ALTER TABLE host_probe_cache ADD COLUMN extracted INTEGER DEFAULT 0")

        # Migration: add RCE status columns if missing
        # Re-fetch columns after potential extracted migration
        cur.execute("PRAGMA table_info(host_probe_cache)")
        columns = [row[1] for row in cur.fetchall()]

        if "rce_status" not in columns:
            cur.execute(
                "ALTER TABLE host_probe_cache ADD COLUMN rce_status TEXT DEFAULT 'not_run'"
            )

        if "rce_verdict_summary" not in columns:
            cur.execute(
                "ALTER TABLE host_probe_cache ADD COLUMN rce_verdict_summary TEXT"
            )

        # Migration: explicit protocol identity on SMB rows (existing rows => 'S')
        cur.execute("PRAGMA table_info(smb_servers)")
        smb_cols = [row[1] for row in cur.fetchall()]
        if smb_cols:
            if "host_type" not in smb_cols:
                cur.execute("ALTER TABLE smb_servers ADD COLUMN host_type TEXT DEFAULT 'S'")
            cur.execute(
                "UPDATE smb_servers SET host_type = 'S' "
                "WHERE host_type IS NULL OR TRIM(host_type) = ''"
            )

        # --- FTP sidecar tables (additive; SMB schema untouched) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ftp_servers (
                id              INTEGER  PRIMARY KEY AUTOINCREMENT,
                ip_address      TEXT     NOT NULL UNIQUE,
                host_type       TEXT     DEFAULT 'F',
                country         TEXT,
                country_code    TEXT,
                port            INTEGER  NOT NULL DEFAULT 21,
                anon_accessible BOOLEAN  NOT NULL DEFAULT FALSE,
                banner          TEXT,
                shodan_data     TEXT,
                first_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                scan_count      INTEGER  DEFAULT 1,
                status          TEXT     DEFAULT 'active',
                notes           TEXT,
                updated_at      DATETIME,
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ftp_access (
                id                     INTEGER  PRIMARY KEY AUTOINCREMENT,
                server_id              INTEGER  NOT NULL,
                session_id             INTEGER,
                accessible             BOOLEAN  NOT NULL DEFAULT FALSE,
                auth_status            TEXT,
                root_listing_available BOOLEAN  DEFAULT FALSE,
                root_entry_count       INTEGER  DEFAULT 0,
                error_message          TEXT,
                test_timestamp         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                access_details         TEXT,
                created_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id)  REFERENCES ftp_servers(id)   ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
            )
            """
        )

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ftp_servers_ip ON ftp_servers(ip_address)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ftp_servers_country ON ftp_servers(country)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ftp_servers_last_seen ON ftp_servers(last_seen)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ftp_access_server ON ftp_access(server_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ftp_access_session ON ftp_access(session_id)"
        )

        # Migration: explicit protocol identity on FTP rows (existing rows => 'F')
        cur.execute("PRAGMA table_info(ftp_servers)")
        ftp_cols = [row[1] for row in cur.fetchall()]
        if "host_type" not in ftp_cols:
            cur.execute("ALTER TABLE ftp_servers ADD COLUMN host_type TEXT DEFAULT 'F'")
        cur.execute(
            "UPDATE ftp_servers SET host_type = 'F' "
            "WHERE host_type IS NULL OR TRIM(host_type) = ''"
        )

        # --- FTP state tables (protocol-specific; parallel to host_user_flags / host_probe_cache) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ftp_user_flags (
                server_id INTEGER PRIMARY KEY,
                favorite  BOOLEAN  DEFAULT 0,
                avoid     BOOLEAN  DEFAULT 0,
                notes     TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ftp_probe_cache (
                server_id           INTEGER  PRIMARY KEY,
                status              TEXT     DEFAULT 'unprobed',
                last_probe_at       DATETIME,
                indicator_matches   INTEGER  DEFAULT 0,
                indicator_samples   TEXT,
                snapshot_path       TEXT,
                accessible_dirs_count INTEGER DEFAULT 0,
                accessible_dirs_list  TEXT,
                extracted           INTEGER  DEFAULT 0,
                rce_status          TEXT     DEFAULT 'not_run',
                rce_verdict_summary TEXT,
                updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
            )
            """
        )

        # Idempotent column backfill for ftp_probe_cache (defensive; mirrors host_probe_cache pattern)
        cur.execute("PRAGMA table_info(ftp_probe_cache)")
        ftp_pc_cols = [row[1] for row in cur.fetchall()]

        if "extracted" not in ftp_pc_cols:
            cur.execute(
                "ALTER TABLE ftp_probe_cache ADD COLUMN extracted INTEGER DEFAULT 0"
            )
        if "rce_status" not in ftp_pc_cols:
            cur.execute(
                "ALTER TABLE ftp_probe_cache ADD COLUMN rce_status TEXT DEFAULT 'not_run'"
            )
        if "rce_verdict_summary" not in ftp_pc_cols:
            cur.execute(
                "ALTER TABLE ftp_probe_cache ADD COLUMN rce_verdict_summary TEXT"
            )
        if "accessible_dirs_count" not in ftp_pc_cols:
            cur.execute(
                "ALTER TABLE ftp_probe_cache ADD COLUMN accessible_dirs_count INTEGER DEFAULT 0"
            )
        if "accessible_dirs_list" not in ftp_pc_cols:
            cur.execute(
                "ALTER TABLE ftp_probe_cache ADD COLUMN accessible_dirs_list TEXT"
            )

        # Backfill visible FTP share counts from latest ftp_access record per server
        # when ftp_probe_cache lacks directory data (legacy rows prior to directory-list persistence).
        cur.execute(
            """
            INSERT INTO ftp_probe_cache (server_id, accessible_dirs_count, accessible_dirs_list, updated_at)
            SELECT
                latest_access.server_id,
                COALESCE(latest_access.root_entry_count, 0) AS accessible_dirs_count,
                '' AS accessible_dirs_list,
                CURRENT_TIMESTAMP
            FROM (
                SELECT a.server_id, a.accessible, a.root_listing_available, a.root_entry_count
                FROM ftp_access a
                INNER JOIN (
                    SELECT server_id, MAX(id) AS max_id
                    FROM ftp_access
                    GROUP BY server_id
                ) latest
                  ON latest.server_id = a.server_id
                 AND latest.max_id    = a.id
            ) latest_access
            WHERE latest_access.accessible = 1
              AND latest_access.root_listing_available = 1
            ON CONFLICT(server_id) DO UPDATE SET
                accessible_dirs_count = CASE
                    WHEN COALESCE(ftp_probe_cache.accessible_dirs_count, 0) = 0
                    THEN excluded.accessible_dirs_count
                    ELSE ftp_probe_cache.accessible_dirs_count
                END,
                accessible_dirs_list = CASE
                    WHEN ftp_probe_cache.accessible_dirs_list IS NULL
                      OR TRIM(ftp_probe_cache.accessible_dirs_list) = ''
                    THEN excluded.accessible_dirs_list
                    ELSE ftp_probe_cache.accessible_dirs_list
                END
            """
        )

        # --- HTTP sidecar tables (additive; SMB and FTP schemas untouched) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS http_servers (
                id           INTEGER  PRIMARY KEY AUTOINCREMENT,
                ip_address   TEXT     NOT NULL UNIQUE,
                host_type    TEXT     DEFAULT 'H',
                country      TEXT,
                country_code TEXT,
                port         INTEGER  NOT NULL DEFAULT 80,
                scheme       TEXT     DEFAULT 'http',
                banner       TEXT,
                title        TEXT,
                shodan_data  TEXT,
                first_seen   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                scan_count   INTEGER  DEFAULT 1,
                status       TEXT     DEFAULT 'active',
                notes        TEXT,
                updated_at   DATETIME,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS http_access (
                id             INTEGER  PRIMARY KEY AUTOINCREMENT,
                server_id      INTEGER  NOT NULL,
                session_id     INTEGER,
                accessible     BOOLEAN  NOT NULL DEFAULT FALSE,
                status_code    INTEGER,
                is_index_page  BOOLEAN  DEFAULT FALSE,
                dir_count      INTEGER  DEFAULT 0,
                file_count     INTEGER  DEFAULT 0,
                tls_verified   BOOLEAN  DEFAULT FALSE,
                error_message  TEXT,
                access_details TEXT,
                test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id)  REFERENCES http_servers(id)   ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES scan_sessions(id)  ON DELETE SET NULL
            )
            """
        )

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_servers_ip      ON http_servers(ip_address)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_servers_country ON http_servers(country)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_servers_seen    ON http_servers(last_seen)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_access_server   ON http_access(server_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_http_access_session  ON http_access(session_id)"
        )

        # Migration: explicit protocol identity on HTTP rows
        cur.execute("PRAGMA table_info(http_servers)")
        http_cols = [row[1] for row in cur.fetchall()]
        if "host_type" not in http_cols:
            cur.execute("ALTER TABLE http_servers ADD COLUMN host_type TEXT DEFAULT 'H'")
        cur.execute(
            "UPDATE http_servers SET host_type = 'H' "
            "WHERE host_type IS NULL OR TRIM(host_type) = ''"
        )

        # --- HTTP state tables (parallel to host_user_flags / host_probe_cache) ---
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS http_user_flags (
                server_id  INTEGER  PRIMARY KEY,
                favorite   BOOLEAN  DEFAULT 0,
                avoid      BOOLEAN  DEFAULT 0,
                notes      TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS http_probe_cache (
                server_id              INTEGER  PRIMARY KEY,
                status                 TEXT     DEFAULT 'unprobed',
                last_probe_at          DATETIME,
                indicator_matches      INTEGER  DEFAULT 0,
                indicator_samples      TEXT,
                snapshot_path          TEXT,
                accessible_dirs_count  INTEGER  DEFAULT 0,
                accessible_dirs_list   TEXT,
                accessible_files_count INTEGER  DEFAULT 0,
                extracted              INTEGER  DEFAULT 0,
                rce_status             TEXT     DEFAULT 'not_run',
                rce_verdict_summary    TEXT,
                updated_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
            )
            """
        )

        # Idempotent column backfill for http_probe_cache (defensive; mirrors ftp_probe_cache pattern)
        cur.execute("PRAGMA table_info(http_probe_cache)")
        http_pc_cols = [row[1] for row in cur.fetchall()]
        if "accessible_files_count" not in http_pc_cols:
            cur.execute(
                "ALTER TABLE http_probe_cache ADD COLUMN accessible_files_count INTEGER DEFAULT 0"
            )
        if "extracted" not in http_pc_cols:
            cur.execute(
                "ALTER TABLE http_probe_cache ADD COLUMN extracted INTEGER DEFAULT 0"
            )
        if "rce_status" not in http_pc_cols:
            cur.execute(
                "ALTER TABLE http_probe_cache ADD COLUMN rce_status TEXT DEFAULT 'not_run'"
            )
        if "rce_verdict_summary" not in http_pc_cols:
            cur.execute(
                "ALTER TABLE http_probe_cache ADD COLUMN rce_verdict_summary TEXT"
            )

        # Protocol coexistence view — rebuilt to include HTTP (drop+create required for new columns)
        cur.execute("DROP VIEW IF EXISTS v_host_protocols")
        cur.execute(
            """
            CREATE VIEW v_host_protocols AS
            SELECT
                ip_address,
                MAX(has_smb)  AS has_smb,
                MAX(has_ftp)  AS has_ftp,
                MAX(has_http) AS has_http,
                CASE
                    WHEN MAX(has_smb)=1 AND MAX(has_ftp)=1 AND MAX(has_http)=1 THEN 'smb+ftp+http'
                    WHEN MAX(has_smb)=1 AND MAX(has_ftp)=1                      THEN 'both'
                    WHEN MAX(has_smb)=1                     AND MAX(has_http)=1 THEN 'smb+http'
                    WHEN                    MAX(has_ftp)=1  AND MAX(has_http)=1 THEN 'ftp+http'
                    WHEN MAX(has_smb)=1                                          THEN 'smb_only'
                    WHEN                    MAX(has_ftp)=1                       THEN 'ftp_only'
                    ELSE                                                               'http_only'
                END AS protocol_presence
            FROM (
                SELECT ip_address, 1 AS has_smb, 0 AS has_ftp, 0 AS has_http FROM smb_servers
                UNION ALL
                SELECT ip_address, 0 AS has_smb, 1 AS has_ftp, 0 AS has_http FROM ftp_servers
                UNION ALL
                SELECT ip_address, 0 AS has_smb, 0 AS has_ftp, 1 AS has_http FROM http_servers
            ) combined
            GROUP BY ip_address
            """
        )

        _normalize_existing_timestamps(cur)
        conn.commit()
    finally:
        if conn:
            conn.close()


def _normalize_existing_timestamps(cur: sqlite3.Cursor) -> None:
    """
    Idempotent one-time migration: normalize ISO T-format timestamps to
    canonical DB format (YYYY-MM-DD HH:MM:SS) in smb_servers, ftp_servers,
    and http_servers.

    The WHERE clause makes this a no-op once all rows are already clean.
    Only catches OperationalError for 'no such table' (brand-new DB where
    a table hasn't been created yet); re-raises all other errors.
    """
    for table in ("smb_servers", "ftp_servers", "http_servers"):
        for col in ("first_seen", "last_seen"):
            try:
                cur.execute(
                    f"UPDATE {table} "
                    f"SET {col} = SUBSTR(REPLACE({col}, 'T', ' '), 1, 19) "
                    f"WHERE {col} LIKE '%T%'"
                )
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    pass  # Table doesn't exist yet on a brand-new DB
                else:
                    raise


def _import_legacy_settings(cur: sqlite3.Cursor) -> None:
    """
    Import favorite/avoid/probe status from legacy GUI settings if paths are found.
    Safe to run multiple times; skips if data already present.
    """
    try:
        settings_path = Path.home() / ".smbseek" / "gui_settings.json"
        if not settings_path.exists():
            return
        data = json.loads(settings_path.read_text(encoding="utf-8"))

        favs = set(data.get("data", {}).get("favorite_servers", []) or [])
        avoids = set(data.get("data", {}).get("avoid_servers", []) or [])
        probe_status_map = data.get("probe", {}).get("status_by_ip", {}) or {}

        if not (favs or avoids or probe_status_map):
            return

        cur.execute("SELECT COUNT(*) FROM host_user_flags")
        if cur.fetchone()[0] > 0:
            return  # assume already imported

        # Build server_id map
        cur.execute("SELECT id, ip_address FROM smb_servers")
        server_map = {row[1]: row[0] for row in cur.fetchall()}

        for ip in favs | avoids:
            server_id = server_map.get(ip)
            if not server_id:
                continue
            cur.execute(
                """
                INSERT INTO host_user_flags (server_id, favorite, avoid, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    favorite=excluded.favorite,
                    avoid=excluded.avoid,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, 1 if ip in favs else 0, 1 if ip in avoids else 0),
            )

        for ip, status in probe_status_map.items():
            server_id = server_map.get(ip)
            if not server_id:
                continue
            cur.execute(
                """
                INSERT INTO host_probe_cache (server_id, status, last_probe_at, indicator_matches, extracted, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, 0, 0, CURRENT_TIMESTAMP)
                ON CONFLICT(server_id) DO UPDATE SET
                    status=excluded.status,
                    last_probe_at=excluded.last_probe_at,
                    extracted=COALESCE(host_probe_cache.extracted, 0),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (server_id, status or "unprobed"),
            )
    except Exception:
        # Silent fail; migration remains best-effort
        pass


def _ensure_core_smb_tables(cur: sqlite3.Cursor) -> None:
    """
    Ensure core SMB runtime tables exist.

    Some legacy databases (or partially initialized files) may not have
    smb_servers / scan_sessions yet. Newer GUI/CLI code assumes both tables
    exist, so we create them idempotently before other migrations run.
    """
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name TEXT DEFAULT 'smbseek',
            scan_type TEXT NOT NULL DEFAULT 'smbseek_unified',
            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            status TEXT DEFAULT 'running',
            total_targets INTEGER DEFAULT 0,
            successful_targets INTEGER DEFAULT 0,
            failed_targets INTEGER DEFAULT 0,
            country_filter TEXT,
            config_snapshot TEXT,
            external_run INTEGER DEFAULT 0,
            notes TEXT,
            updated_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Legacy compatibility: CREATE TABLE IF NOT EXISTS does not alter an
    # existing table, so older scan_sessions schemas may be missing columns.
    cur.execute("PRAGMA table_info(scan_sessions)")
    scan_session_cols = {row[1] for row in cur.fetchall()}

    def _ensure_scan_sessions_col(name: str, column_def: str) -> None:
        if name in scan_session_cols:
            return
        cur.execute(f"ALTER TABLE scan_sessions ADD COLUMN {name} {column_def}")
        scan_session_cols.add(name)

    _ensure_scan_sessions_col("tool_name", "TEXT DEFAULT 'smbseek'")
    _ensure_scan_sessions_col("scan_type", "TEXT DEFAULT 'smbseek_unified'")
    _ensure_scan_sessions_col("started_at", "DATETIME")
    _ensure_scan_sessions_col("completed_at", "DATETIME")
    _ensure_scan_sessions_col("total_targets", "INTEGER DEFAULT 0")
    _ensure_scan_sessions_col("successful_targets", "INTEGER DEFAULT 0")
    _ensure_scan_sessions_col("failed_targets", "INTEGER DEFAULT 0")
    _ensure_scan_sessions_col("country_filter", "TEXT")
    _ensure_scan_sessions_col("config_snapshot", "TEXT")
    _ensure_scan_sessions_col("external_run", "INTEGER DEFAULT 0")
    _ensure_scan_sessions_col("notes", "TEXT")
    _ensure_scan_sessions_col("updated_at", "DATETIME")

    # Backfill critical identifiers used by query/index paths.
    cur.execute(
        "UPDATE scan_sessions SET tool_name = 'smbseek' "
        "WHERE tool_name IS NULL OR TRIM(tool_name) = ''"
    )
    cur.execute(
        "UPDATE scan_sessions SET scan_type = 'smbseek_unified' "
        "WHERE scan_type IS NULL OR TRIM(scan_type) = ''"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS smb_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip_address TEXT NOT NULL UNIQUE,
            host_type TEXT DEFAULT 'S',
            country TEXT,
            country_code TEXT,
            auth_method TEXT,
            shodan_data TEXT,
            first_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            scan_count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            notes TEXT,
            updated_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS share_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            share_name TEXT NOT NULL,
            accessible BOOLEAN NOT NULL DEFAULT FALSE,
            auth_status TEXT,
            permissions TEXT,
            share_type TEXT,
            share_comment TEXT,
            test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            access_details TEXT,
            error_message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS file_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            share_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            file_type TEXT,
            file_extension TEXT,
            mime_type TEXT,
            last_modified DATETIME,
            is_ransomware_indicator BOOLEAN DEFAULT FALSE,
            is_sensitive BOOLEAN DEFAULT FALSE,
            discovery_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vulnerabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            vuln_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            evidence TEXT,
            remediation TEXT,
            cvss_score DECIMAL(3,1),
            cve_ids TEXT,
            discovery_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'open',
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS failure_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            ip_address TEXT NOT NULL,
            failure_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            failure_type TEXT,
            failure_reason TEXT,
            shodan_data TEXT,
            analysis_results TEXT,
            retry_count INTEGER DEFAULT 0,
            last_retry_timestamp DATETIME,
            resolved BOOLEAN DEFAULT FALSE,
            resolution_notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
        )
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_smb_servers_ip ON smb_servers(ip_address)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_smb_servers_country ON smb_servers(country)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_smb_servers_last_seen ON smb_servers(last_seen)"
    )
    if "timestamp" in scan_session_cols:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_sessions_timestamp ON scan_sessions(timestamp)"
        )
    if "tool_name" in scan_session_cols:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_sessions_tool ON scan_sessions(tool_name)"
        )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_access_server ON share_access(server_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_access_session ON share_access(session_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_file_manifests_server ON file_manifests(server_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_file_manifests_session ON file_manifests(session_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_vulnerabilities_server ON vulnerabilities(server_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_vulnerabilities_session ON vulnerabilities(session_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_failure_logs_ip ON failure_logs(ip_address)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_failure_logs_timestamp ON failure_logs(failure_timestamp)"
    )


def _backfill_smb_servers_from_legacy_servers(cur: sqlite3.Cursor) -> None:
    """
    One-time compatibility import for legacy 'servers' table databases.

    Copies rows into smb_servers using best-effort column mapping and leaves
    existing smb_servers rows untouched.
    """
    cur.execute("PRAGMA table_info(servers)")
    legacy_cols = {row[1] for row in cur.fetchall()}
    if not legacy_cols or "ip_address" not in legacy_cols:
        return

    def _coalesce(*candidates: str) -> str:
        available = [c for c in candidates if c in legacy_cols]
        if not available:
            return "CURRENT_TIMESTAMP"
        return f"COALESCE({', '.join(available)}, CURRENT_TIMESTAMP)"

    country_expr = "country" if "country" in legacy_cols else "NULL"
    country_code_expr = "country_code" if "country_code" in legacy_cols else "NULL"
    auth_method_expr = "auth_method" if "auth_method" in legacy_cols else "NULL"
    shodan_data_expr = "shodan_data" if "shodan_data" in legacy_cols else "NULL"
    notes_expr = "notes" if "notes" in legacy_cols else "NULL"
    updated_at_expr = "updated_at" if "updated_at" in legacy_cols else "NULL"
    first_seen_expr = _coalesce("first_seen", "created_at", "last_seen", "updated_at")
    last_seen_expr = _coalesce("last_seen", "updated_at", "first_seen", "created_at")
    scan_count_expr = "COALESCE(scan_count, 1)" if "scan_count" in legacy_cols else "1"
    status_expr = "COALESCE(status, 'active')" if "status" in legacy_cols else "'active'"
    created_at_expr = _coalesce("created_at", "first_seen", "last_seen", "updated_at")

    cur.execute(
        f"""
        INSERT INTO smb_servers (
            ip_address,
            country,
            country_code,
            auth_method,
            shodan_data,
            first_seen,
            last_seen,
            scan_count,
            status,
            notes,
            updated_at,
            created_at
        )
        SELECT
            TRIM(ip_address),
            {country_expr},
            {country_code_expr},
            {auth_method_expr},
            {shodan_data_expr},
            {first_seen_expr},
            {last_seen_expr},
            {scan_count_expr},
            {status_expr},
            {notes_expr},
            {updated_at_expr},
            {created_at_expr}
        FROM servers
        WHERE ip_address IS NOT NULL
          AND TRIM(ip_address) <> ''
        ON CONFLICT(ip_address) DO NOTHING
        """
    )


__all__ = ["run_migrations"]
