"""
Unit tests for gui.utils.db_merge_import_engine.

Headless tests (no Tkinter) that validate the 7 extracted import functions
and their module-private helpers in isolation. Each test uses in-memory SQLite.
"""

import sqlite3
from datetime import datetime

import pytest

from gui.utils.db_merge_import_engine import (
    import_failure_logs,
    import_file_manifests,
    import_ftp_access,
    import_http_access,
    import_share_access,
    import_share_credentials,
    import_vulnerabilities,
)

_MIN_DATE = datetime(1970, 1, 1)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(ts):
    if not ts:
        return _MIN_DATE
    try:
        return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return _MIN_DATE


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _create_share_access(conn):
    conn.execute("""
        CREATE TABLE share_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, session_id INTEGER, share_name TEXT,
            accessible INTEGER, auth_status TEXT, permissions TEXT,
            share_type TEXT, share_comment TEXT, test_timestamp TEXT,
            access_details TEXT, error_message TEXT
        )
    """)


def _create_ftp_access(conn, include_extra_col=True):
    cols = (
        "id INTEGER PRIMARY KEY AUTOINCREMENT, server_id INTEGER, session_id INTEGER, "
        "accessible INTEGER, auth_status TEXT, root_listing_available INTEGER, "
        "root_entry_count INTEGER, error_message TEXT, test_timestamp TEXT, access_details TEXT"
    )
    conn.execute(f"CREATE TABLE ftp_access ({cols})")


def _create_http_access(conn):
    conn.execute("""
        CREATE TABLE http_access (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, session_id INTEGER, accessible INTEGER,
            status_code INTEGER, is_index_page INTEGER, dir_count INTEGER,
            file_count INTEGER, tls_verified INTEGER, error_message TEXT,
            access_details TEXT, test_timestamp TEXT
        )
    """)


def _create_share_credentials(conn):
    conn.execute("""
        CREATE TABLE share_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, share_name TEXT, username TEXT, password TEXT,
            source TEXT, session_id INTEGER, last_verified_at TEXT,
            UNIQUE(server_id, share_name, username, password)
        )
    """)


def _create_file_manifests(conn):
    conn.execute("""
        CREATE TABLE file_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, session_id INTEGER, share_name TEXT,
            file_path TEXT, file_name TEXT, file_size INTEGER,
            file_type TEXT, file_extension TEXT, mime_type TEXT,
            last_modified TEXT, is_ransomware_indicator INTEGER,
            is_sensitive INTEGER, discovery_timestamp TEXT, metadata TEXT
        )
    """)


def _create_vulnerabilities(conn):
    conn.execute("""
        CREATE TABLE vulnerabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER, session_id INTEGER, vuln_type TEXT,
            severity TEXT, title TEXT, description TEXT, evidence TEXT,
            remediation TEXT, cvss_score REAL, cve_ids TEXT,
            discovery_timestamp TEXT, status TEXT, notes TEXT
        )
    """)


def _create_failure_logs(conn):
    conn.execute("""
        CREATE TABLE failure_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER, ip_address TEXT, failure_timestamp TEXT,
            failure_type TEXT, failure_reason TEXT, shodan_data TEXT,
            analysis_results TEXT, retry_count INTEGER,
            last_retry_timestamp TEXT
        )
    """)


def _create_smb_servers(conn):
    conn.execute("""
        CREATE TABLE smb_servers (
            id INTEGER PRIMARY KEY, ip_address TEXT
        )
    """)


# ---------------------------------------------------------------------------
# import_share_access
# ---------------------------------------------------------------------------

def test_import_share_access_skips_when_external_not_newer():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_share_access(ext_conn)
    _create_share_access(cur_conn)

    # Current DB has a newer record
    cur_conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible, "
        "auth_status, permissions, share_type, share_comment, test_timestamp, "
        "access_details, error_message) VALUES (1, 0, 'share1', 1, 'anon', NULL, "
        "NULL, NULL, '2025-01-02 10:00:00', NULL, NULL)"
    )
    cur_conn.commit()

    # External DB has older record for the same share (ext server_id=10 → maps to 1)
    ext_conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible, "
        "auth_status, permissions, share_type, share_comment, test_timestamp, "
        "access_details, error_message) VALUES (10, 0, 'share1', 1, 'anon', NULL, "
        "NULL, NULL, '2025-01-01 10:00:00', NULL, NULL)"
    )
    ext_conn.commit()

    count = import_share_access(
        ext_conn, cur_conn, {10: 1}, import_session_id=99, parse_ts_fn=_parse_ts
    )
    assert count == 0
    # Original timestamp must be untouched
    row = cur_conn.execute(
        "SELECT test_timestamp FROM share_access WHERE server_id=1 AND share_name='share1'"
    ).fetchone()
    assert row['test_timestamp'] == '2025-01-02 10:00:00'


def test_import_share_access_updates_when_external_newer():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_share_access(ext_conn)
    _create_share_access(cur_conn)

    # Current DB has an older record
    cur_conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible, "
        "auth_status, permissions, share_type, share_comment, test_timestamp, "
        "access_details, error_message) VALUES (1, 0, 'share1', 1, 'anon', NULL, "
        "NULL, NULL, '2025-01-01 10:00:00', NULL, NULL)"
    )
    cur_conn.commit()

    # External DB has newer record
    ext_conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible, "
        "auth_status, permissions, share_type, share_comment, test_timestamp, "
        "access_details, error_message) VALUES (10, 0, 'share1', 1, 'guest', NULL, "
        "NULL, NULL, '2025-01-02 10:00:00', NULL, NULL)"
    )
    ext_conn.commit()

    count = import_share_access(
        ext_conn, cur_conn, {10: 1}, import_session_id=99, parse_ts_fn=_parse_ts
    )
    assert count == 1
    row = cur_conn.execute(
        "SELECT auth_status, test_timestamp FROM share_access WHERE server_id=1 AND share_name='share1'"
    ).fetchone()
    assert row['test_timestamp'] == '2025-01-02 10:00:00'
    assert row['auth_status'] == 'guest'


# ---------------------------------------------------------------------------
# import_ftp_access
# ---------------------------------------------------------------------------

def test_import_ftp_access_missing_required_columns_returns_zero():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    # Create ftp_access WITHOUT the 'accessible' column in ext
    ext_conn.execute(
        "CREATE TABLE ftp_access (id INTEGER PRIMARY KEY, server_id INTEGER, test_timestamp TEXT)"
    )
    _create_ftp_access(cur_conn)

    required_read = {'server_id', 'test_timestamp', 'accessible', 'auth_status'}
    required_target = {'server_id', 'test_timestamp'}

    count = import_ftp_access(
        ext_conn, cur_conn, {1: 1}, import_session_id=99,
        parse_ts_fn=_parse_ts, required_read=required_read, required_target=required_target,
    )
    assert count == 0


def test_import_ftp_access_inserts_when_no_existing_record():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_ftp_access(ext_conn)
    _create_ftp_access(cur_conn)

    ext_conn.execute(
        "INSERT INTO ftp_access (server_id, session_id, accessible, auth_status, "
        "root_listing_available, root_entry_count, error_message, test_timestamp, "
        "access_details) VALUES (10, 0, 1, 'anon', 1, 5, NULL, '2025-01-01 10:00:00', NULL)"
    )
    ext_conn.commit()

    required = {'server_id', 'accessible', 'auth_status', 'root_listing_available',
                'root_entry_count', 'error_message', 'test_timestamp', 'access_details'}
    count = import_ftp_access(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        parse_ts_fn=_parse_ts, required_read=required, required_target=required,
    )
    assert count == 1
    row = cur_conn.execute("SELECT server_id, accessible FROM ftp_access").fetchone()
    assert row['server_id'] == 1
    assert row['accessible'] == 1


# ---------------------------------------------------------------------------
# import_http_access
# ---------------------------------------------------------------------------

def test_import_http_access_missing_required_columns_returns_zero():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    # ext table missing 'status_code'
    ext_conn.execute(
        "CREATE TABLE http_access (id INTEGER PRIMARY KEY, server_id INTEGER, test_timestamp TEXT)"
    )
    _create_http_access(cur_conn)

    required_read = {'server_id', 'test_timestamp', 'accessible', 'status_code'}
    required_target = {'server_id', 'test_timestamp'}

    count = import_http_access(
        ext_conn, cur_conn, {1: 1}, import_session_id=99,
        parse_ts_fn=_parse_ts, required_read=required_read, required_target=required_target,
    )
    assert count == 0


# ---------------------------------------------------------------------------
# import_share_credentials
# ---------------------------------------------------------------------------

def test_import_share_credentials_insert_or_ignore_dedupe():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_share_credentials(ext_conn)
    _create_share_credentials(cur_conn)

    # Pre-insert the same credential into cur_conn so it already exists
    cur_conn.execute(
        "INSERT INTO share_credentials (server_id, share_name, username, password, "
        "source, session_id, last_verified_at) VALUES (1, 'share1', 'admin', 'pass', "
        "'scan', 0, NULL)"
    )
    cur_conn.commit()

    # External DB has the same credential (server_id=10 → maps to 1)
    ext_conn.execute(
        "INSERT INTO share_credentials (server_id, share_name, username, password, "
        "source, session_id, last_verified_at) VALUES (10, 'share1', 'admin', 'pass', "
        "'scan', 0, NULL)"
    )
    ext_conn.commit()

    required = {'server_id', 'share_name', 'username', 'password', 'source', 'last_verified_at'}
    count = import_share_credentials(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        required_read=required, required_target=required,
    )
    # Duplicate must be silently ignored
    assert count == 0
    total = cur_conn.execute("SELECT COUNT(*) FROM share_credentials").fetchone()[0]
    assert total == 1


def test_import_share_credentials_inserts_new_record():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_share_credentials(ext_conn)
    _create_share_credentials(cur_conn)

    ext_conn.execute(
        "INSERT INTO share_credentials (server_id, share_name, username, password, "
        "source, session_id, last_verified_at) VALUES (10, 'share1', 'admin', 'pass', "
        "'scan', 0, NULL)"
    )
    ext_conn.commit()

    required = {'server_id', 'share_name', 'username', 'password', 'source', 'last_verified_at'}
    count = import_share_credentials(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        required_read=required, required_target=required,
    )
    assert count == 1
    row = cur_conn.execute("SELECT server_id, username FROM share_credentials").fetchone()
    assert row['server_id'] == 1
    assert row['username'] == 'admin'


# ---------------------------------------------------------------------------
# import_file_manifests
# ---------------------------------------------------------------------------

def test_import_file_manifests_dedupe_by_key_and_timestamp():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_file_manifests(ext_conn)
    _create_file_manifests(cur_conn)

    # Current DB already has this file with a newer timestamp
    cur_conn.execute(
        "INSERT INTO file_manifests (server_id, session_id, share_name, file_path, "
        "file_name, file_size, file_type, file_extension, mime_type, last_modified, "
        "is_ransomware_indicator, is_sensitive, discovery_timestamp, metadata) "
        "VALUES (1, 0, 'share1', '/docs/readme.txt', 'readme.txt', 100, 'text', "
        "'.txt', 'text/plain', NULL, 0, 0, '2025-01-02 10:00:00', NULL)"
    )
    cur_conn.commit()

    # External has same file key but older timestamp
    ext_conn.execute(
        "INSERT INTO file_manifests (server_id, session_id, share_name, file_path, "
        "file_name, file_size, file_type, file_extension, mime_type, last_modified, "
        "is_ransomware_indicator, is_sensitive, discovery_timestamp, metadata) "
        "VALUES (10, 0, 'share1', '/docs/readme.txt', 'readme.txt', 100, 'text', "
        "'.txt', 'text/plain', NULL, 0, 0, '2025-01-01 10:00:00', NULL)"
    )
    ext_conn.commit()

    required = {'server_id', 'share_name', 'file_path', 'file_name', 'file_size',
                'file_type', 'file_extension', 'mime_type', 'last_modified',
                'is_ransomware_indicator', 'is_sensitive', 'discovery_timestamp', 'metadata'}
    count = import_file_manifests(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        parse_ts_fn=_parse_ts, required_read=required, required_target=required,
    )
    assert count == 0
    total = cur_conn.execute("SELECT COUNT(*) FROM file_manifests").fetchone()[0]
    assert total == 1


# ---------------------------------------------------------------------------
# import_vulnerabilities
# ---------------------------------------------------------------------------

def test_import_vulnerabilities_dedupe_by_vuln_type_and_cve_ids():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_vulnerabilities(ext_conn)
    _create_vulnerabilities(cur_conn)

    # Current DB already has this vulnerability
    cur_conn.execute(
        "INSERT INTO vulnerabilities (server_id, session_id, vuln_type, severity, "
        "title, description, evidence, remediation, cvss_score, cve_ids, "
        "discovery_timestamp, status, notes) "
        "VALUES (1, 0, 'ms17-010', 'critical', 'EternalBlue', NULL, NULL, NULL, "
        "9.8, 'CVE-2017-0144', '2025-01-01 00:00:00', 'open', NULL)"
    )
    cur_conn.commit()

    # External has the same vuln key (server_id=10 → maps to 1)
    ext_conn.execute(
        "INSERT INTO vulnerabilities (server_id, session_id, vuln_type, severity, "
        "title, description, evidence, remediation, cvss_score, cve_ids, "
        "discovery_timestamp, status, notes) "
        "VALUES (10, 0, 'ms17-010', 'critical', 'EternalBlue', NULL, NULL, NULL, "
        "9.8, 'CVE-2017-0144', '2025-01-02 00:00:00', 'open', NULL)"
    )
    ext_conn.commit()

    required = {'server_id', 'vuln_type', 'severity', 'title', 'description',
                'evidence', 'remediation', 'cvss_score', 'cve_ids',
                'discovery_timestamp', 'status', 'notes'}
    count = import_vulnerabilities(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        required_read=required, required_target=required,
    )
    assert count == 0
    total = cur_conn.execute("SELECT COUNT(*) FROM vulnerabilities").fetchone()[0]
    assert total == 1


# ---------------------------------------------------------------------------
# import_failure_logs
# ---------------------------------------------------------------------------

def test_import_failure_logs_filters_to_imported_ips_and_updates_retry_count():
    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_failure_logs(ext_conn)
    _create_failure_logs(cur_conn)
    _create_smb_servers(ext_conn)
    _create_smb_servers(cur_conn)

    # Current DB: server 1 with ip 10.0.0.1
    cur_conn.execute("INSERT INTO smb_servers (id, ip_address) VALUES (1, '10.0.0.1')")
    # Pre-existing failure log
    cur_conn.execute(
        "INSERT INTO failure_logs (session_id, ip_address, failure_timestamp, failure_type, "
        "failure_reason, shodan_data, analysis_results, retry_count) "
        "VALUES (0, '10.0.0.1', '2025-01-01 00:00:00', 'auth', 'timeout', NULL, NULL, 2)"
    )
    cur_conn.commit()

    # External DB: server 10 (mapped to 1) with same ip, plus unrelated server 99
    ext_conn.execute("INSERT INTO smb_servers (id, ip_address) VALUES (10, '10.0.0.1')")
    ext_conn.execute("INSERT INTO smb_servers (id, ip_address) VALUES (99, '192.168.0.1')")
    # Existing failure log (should increment retry_count)
    ext_conn.execute(
        "INSERT INTO failure_logs (session_id, ip_address, failure_timestamp, failure_type, "
        "failure_reason, shodan_data, analysis_results, retry_count) "
        "VALUES (0, '10.0.0.1', '2025-01-02 00:00:00', 'auth', 'timeout', NULL, NULL, 3)"
    )
    # New failure log (different type, should be inserted)
    ext_conn.execute(
        "INSERT INTO failure_logs (session_id, ip_address, failure_timestamp, failure_type, "
        "failure_reason, shodan_data, analysis_results, retry_count) "
        "VALUES (0, '10.0.0.1', '2025-01-02 00:00:00', 'smb', 'refused', NULL, NULL, 1)"
    )
    # Log for unrelated IP not in id_mapping — must be filtered out
    ext_conn.execute(
        "INSERT INTO failure_logs (session_id, ip_address, failure_timestamp, failure_type, "
        "failure_reason, shodan_data, analysis_results, retry_count) "
        "VALUES (0, '192.168.0.1', '2025-01-02 00:00:00', 'auth', 'timeout', NULL, NULL, 1)"
    )
    ext_conn.commit()

    required = {'ip_address', 'failure_timestamp', 'failure_type', 'failure_reason',
                'shodan_data', 'analysis_results', 'retry_count'}
    count = import_failure_logs(
        ext_conn, cur_conn, {10: 1}, import_session_id=99,
        required_read=required, required_target=required,
    )
    # Only the new 'smb' type should be counted as newly imported
    assert count == 1

    # Original 'auth' log should have its retry_count incremented
    auth_row = cur_conn.execute(
        "SELECT retry_count FROM failure_logs WHERE ip_address='10.0.0.1' AND failure_type='auth'"
    ).fetchone()
    assert auth_row['retry_count'] == 5  # 2 (original) + 3 (from external)

    # New 'smb' log must exist
    smb_row = cur_conn.execute(
        "SELECT failure_type FROM failure_logs WHERE ip_address='10.0.0.1' AND failure_type='smb'"
    ).fetchone()
    assert smb_row is not None

    # Unrelated IP must not appear
    other = cur_conn.execute(
        "SELECT 1 FROM failure_logs WHERE ip_address='192.168.0.1'"
    ).fetchone()
    assert other is None


# ---------------------------------------------------------------------------
# Adapter contract smoke test (required)
# ---------------------------------------------------------------------------

def test_adapter_delegates_import_share_access():
    """Verify the delegate in db_merge_engine forwards correctly to db_merge_import_engine."""
    from gui.utils.db_merge_engine import import_share_access as delegate

    ext_conn = _make_conn()
    cur_conn = _make_conn()
    _create_share_access(ext_conn)
    _create_share_access(cur_conn)

    ext_conn.execute(
        "INSERT INTO share_access (server_id, session_id, share_name, accessible, "
        "auth_status, permissions, share_type, share_comment, test_timestamp, "
        "access_details, error_message) VALUES (10, 0, 'data', 1, 'anon', NULL, "
        "NULL, NULL, '2025-06-01 08:00:00', NULL, NULL)"
    )
    ext_conn.commit()

    count = delegate(
        ext_conn, cur_conn, {10: 1}, import_session_id=42, parse_ts_fn=_parse_ts
    )
    assert count == 1
    row = cur_conn.execute(
        "SELECT server_id, share_name FROM share_access"
    ).fetchone()
    assert row['server_id'] == 1
    assert row['share_name'] == 'data'
