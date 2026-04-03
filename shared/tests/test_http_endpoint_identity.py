"""
Regression tests for HTTP endpoint identity (ip + port).

Covers:
- Legacy UNIQUE(ip_address) schema migration to UNIQUE(ip_address, port).
- HttpPersistence upsert behavior across multiple endpoints on one IP.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from commands.http.models import HttpCandidate
from shared.database import HttpPersistence
from shared.db_migrations import run_migrations


def _unique_index_columns(db_path: Path, table: str) -> set[tuple[str, ...]]:
    """Return unique-index column tuples for a table."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA index_list({table})")
        unique_sets: set[tuple[str, ...]] = set()
        for row in cur.fetchall():
            # seq, name, unique, origin, partial
            if int(row[2]) != 1:
                continue
            idx_name = str(row[1]).replace('"', '""')
            cur.execute(f'PRAGMA index_info("{idx_name}")')
            cols = tuple(r[2] for r in cur.fetchall())
            unique_sets.add(cols)
        return unique_sets
    finally:
        conn.close()


def test_migration_converts_legacy_http_unique_ip_to_endpoint(tmp_path):
    db = tmp_path / "legacy_http.db"

    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE http_servers (
                id           INTEGER  PRIMARY KEY AUTOINCREMENT,
                ip_address   TEXT     NOT NULL UNIQUE,
                port         INTEGER  NOT NULL DEFAULT 80,
                scheme       TEXT     DEFAULT 'http',
                first_seen   DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE http_user_flags (
                server_id INTEGER PRIMARY KEY,
                favorite  BOOLEAN DEFAULT FALSE,
                avoid     BOOLEAN DEFAULT FALSE,
                notes     TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (server_id) REFERENCES http_servers(id) ON DELETE CASCADE
            );
            """
        )
        conn.execute(
            "INSERT INTO http_servers (ip_address, port, scheme) VALUES (?, ?, ?)",
            ("198.51.100.10", 8080, "http"),
        )
        conn.execute(
            "INSERT INTO http_user_flags (server_id, favorite, avoid) VALUES (?, ?, ?)",
            (1, 1, 0),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db))
    run_migrations(str(db))  # idempotent

    uniques = _unique_index_columns(db, "http_servers")
    assert ("ip_address", "port") in uniques
    assert ("ip_address",) not in uniques

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT id, ip_address, port, scheme FROM http_servers WHERE id = 1"
        ).fetchone()
        child = conn.execute(
            "SELECT server_id, favorite, avoid FROM http_user_flags WHERE server_id = 1"
        ).fetchone()
    finally:
        conn.close()

    assert row == (1, "198.51.100.10", 8080, "http")
    assert child == (1, 1, 0)


def test_migration_adds_endpoint_unique_when_http_has_no_unique(tmp_path):
    db = tmp_path / "legacy_http_no_unique.db"

    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            CREATE TABLE http_servers (
                id           INTEGER  PRIMARY KEY AUTOINCREMENT,
                ip_address   TEXT     NOT NULL,
                port         INTEGER  NOT NULL DEFAULT 80,
                scheme       TEXT     DEFAULT 'http',
                first_seen   DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen    DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO http_servers (ip_address, port, scheme) VALUES (?, ?, ?)",
            ("198.51.100.21", 8080, "http"),
        )
        conn.execute(
            "INSERT INTO http_servers (ip_address, port, scheme) VALUES (?, ?, ?)",
            ("198.51.100.21", 8443, "https"),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db))

    uniques = _unique_index_columns(db, "http_servers")
    assert ("ip_address", "port") in uniques

    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT ip_address, port, scheme FROM http_servers ORDER BY port"
        ).fetchall()
    finally:
        conn.close()

    assert rows == [
        ("198.51.100.21", 8080, "http"),
        ("198.51.100.21", 8443, "https"),
    ]


def test_migration_handles_legacy_view_that_references_main_http_servers(tmp_path):
    db = tmp_path / "legacy_http_view.db"
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    try:
        cur = conn.cursor()
        cur.execute("DROP VIEW IF EXISTS v_host_protocols")
        cur.execute("DROP TABLE IF EXISTS http_probe_cache")
        cur.execute("DROP TABLE IF EXISTS http_user_flags")
        cur.execute("DROP TABLE IF EXISTS http_access")
        cur.execute("ALTER TABLE http_servers RENAME TO http_servers_old")
        cur.execute(
            """
            CREATE TABLE http_servers (
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
            "INSERT INTO http_servers (id, ip_address, port, scheme) VALUES (?, ?, ?, ?)",
            (1, "198.51.100.55", 8080, "http"),
        )
        cur.execute("DROP TABLE http_servers_old")
        cur.execute(
            """
            CREATE VIEW v_host_protocols AS
            SELECT ip_address,
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
              SELECT ip_address, 1 AS has_smb, 0 AS has_ftp, 0 AS has_http FROM main.smb_servers
              UNION ALL
              SELECT ip_address, 0 AS has_smb, 1 AS has_ftp, 0 AS has_http FROM main.ftp_servers
              UNION ALL
              SELECT ip_address, 0 AS has_smb, 0 AS has_ftp, 1 AS has_http FROM main.http_servers
            ) combined
            GROUP BY ip_address
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Must no longer fail with:
    # "error in view v_host_protocols: no such table: main.http_servers"
    run_migrations(str(db))

    uniques = _unique_index_columns(db, "http_servers")
    assert ("ip_address", "port") in uniques

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT ip_address, port, scheme FROM http_servers WHERE id = 1"
        ).fetchone()
        view_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_host_protocols'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert row == ("198.51.100.55", 8080, "http")
    assert "FROM http_servers" in view_sql


def test_http_persistence_upsert_tracks_endpoints_per_ip(tmp_path):
    db = tmp_path / "http_endpoints.db"
    run_migrations(str(db))

    persistence = HttpPersistence(str(db))

    server_id_8080 = persistence.upsert_http_server(
        ip="203.0.113.25",
        country="US",
        country_code="US",
        port=8080,
        scheme="http",
        banner="banner-a",
        title="title-a",
        shodan_data="{}",
    )
    server_id_8443 = persistence.upsert_http_server(
        ip="203.0.113.25",
        country="US",
        country_code="US",
        port=8443,
        scheme="https",
        banner="banner-b",
        title="title-b",
        shodan_data="{}",
    )
    server_id_8080_again = persistence.upsert_http_server(
        ip="203.0.113.25",
        country="US",
        country_code="US",
        port=8080,
        scheme="http",
        banner="banner-a2",
        title="title-a2",
        shodan_data="{}",
    )

    assert server_id_8080 != server_id_8443
    assert server_id_8080_again == server_id_8080

    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM http_servers WHERE ip_address = ?",
            ("203.0.113.25",),
        ).fetchone()[0]
        scan_count_8080 = conn.execute(
            "SELECT scan_count FROM http_servers WHERE ip_address = ? AND port = ?",
            ("203.0.113.25", 8080),
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 2
    assert int(scan_count_8080) == 2


def test_http_filter_recent_candidates_uses_endpoint_identity(tmp_path):
    db = tmp_path / "http_filter.db"
    run_migrations(str(db))

    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(
            """
            INSERT INTO http_servers (id, ip_address, port, scheme, last_seen)
            VALUES (1, '203.0.113.50', 8080, 'http', datetime('now'));
            INSERT INTO http_servers (id, ip_address, port, scheme, last_seen)
            VALUES (2, '203.0.113.50', 8443, 'https', datetime('now'));
            INSERT INTO http_servers (id, ip_address, port, scheme, last_seen)
            VALUES (3, '203.0.113.51', 8080, 'http', datetime('now', '-40 days'));
            INSERT INTO http_servers (id, ip_address, port, scheme, last_seen)
            VALUES (4, '203.0.113.52', 8080, 'http', datetime('now'));

            INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page,
                                     dir_count, file_count, tls_verified, error_message, access_details)
            VALUES (1, NULL, 1, 200, 1, 1, 1, 0, '', '{}');
            INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page,
                                     dir_count, file_count, tls_verified, error_message, access_details)
            VALUES (2, NULL, 0, 500, 0, 0, 0, 0, 'fail', '{}');
            INSERT INTO http_access (server_id, session_id, accessible, status_code, is_index_page,
                                     dir_count, file_count, tls_verified, error_message, access_details)
            VALUES (3, NULL, 1, 200, 1, 1, 1, 0, '', '{}');
            """
        )
        conn.commit()
    finally:
        conn.close()

    persistence = HttpPersistence(str(db))
    candidates = [
        HttpCandidate(
            ip="203.0.113.50", port=8080, scheme="http", banner="", title="",
            country="US", country_code="US", shodan_data={}
        ),
        HttpCandidate(
            ip="203.0.113.50", port=8443, scheme="https", banner="", title="",
            country="US", country_code="US", shodan_data={}
        ),
        HttpCandidate(
            ip="203.0.113.51", port=8080, scheme="http", banner="", title="",
            country="US", country_code="US", shodan_data={}
        ),
        HttpCandidate(
            ip="203.0.113.52", port=8080, scheme="http", banner="", title="",
            country="US", country_code="US", shodan_data={}
        ),
        HttpCandidate(
            ip="203.0.113.53", port=8080, scheme="http", banner="", title="",
            country="US", country_code="US", shodan_data={}
        ),
    ]

    filtered, stats = persistence.filter_recent_candidates(candidates, rescan_after_days=30)
    filtered_endpoints = [(c.ip, c.port) for c in filtered]

    assert filtered_endpoints == [
        ("203.0.113.50", 8443),  # recent failure endpoint retried
        ("203.0.113.51", 8080),  # old enough
        ("203.0.113.52", 8080),  # recent with no access history -> retry
        ("203.0.113.53", 8080),  # new endpoint
    ]
    assert stats["total"] == 5
    assert stats["known"] == 4
    assert stats["new"] == 1
    assert stats["skipped_recent"] == 1
    assert stats["retried_recent_failures"] == 2
    assert stats["old_enough"] == 1
    assert stats["to_scan"] == 4
