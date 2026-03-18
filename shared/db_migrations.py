"""
Lightweight, idempotent database migrations for SMBSeek.

Currently installs:
- share_credentials: stores per-share credentials discovered via Pry (or future sources).
- host_probe_cache: caches probe status including RCE analysis results.
- ftp_servers: FTP server registry (sidecar, coexists with smb_servers per IP).
- ftp_access: per-session FTP access summary.
- ftp_user_flags: per-FTP-server user flags (favorite/avoid/notes), parallel to host_user_flags.
- ftp_probe_cache: per-FTP-server probe cache (status/indicators/extracted/rce), parallel to host_probe_cache.
- v_host_protocols: view resolving has_smb / has_ftp / protocol_presence per IP.
- Timestamp canonicalization: normalizes existing T-format timestamps in smb_servers/ftp_servers
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

        # Protocol coexistence view — must be created after both FTP tables exist
        cur.execute(
            """
            CREATE VIEW IF NOT EXISTS v_host_protocols AS
            SELECT
                ip_address,
                MAX(has_smb) AS has_smb,
                MAX(has_ftp) AS has_ftp,
                CASE
                    WHEN MAX(has_smb) = 1 AND MAX(has_ftp) = 1 THEN 'both'
                    WHEN MAX(has_smb) = 1                       THEN 'smb_only'
                    ELSE                                              'ftp_only'
                END AS protocol_presence
            FROM (
                SELECT ip_address, 1 AS has_smb, 0 AS has_ftp FROM smb_servers
                UNION ALL
                SELECT ip_address, 0 AS has_smb, 1 AS has_ftp FROM ftp_servers
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
    canonical DB format (YYYY-MM-DD HH:MM:SS) in smb_servers and ftp_servers.

    The WHERE clause makes this a no-op once all rows are already clean.
    Only catches OperationalError for 'no such table' (brand-new DB where
    ftp_servers hasn't been created yet); re-raises all other errors.
    """
    for table in ("smb_servers", "ftp_servers"):
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


__all__ = ["run_migrations"]
