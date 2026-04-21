"""
Unit tests for DBToolsEngine - database management operations.

Tests cover schema validation, merge operations, export/backup, statistics,
and maintenance operations. Uses temporary SQLite databases for isolation.
"""

import os
import csv
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.db_tools_engine import (
    DBToolsEngine,
    MergeConflictStrategy,
    MergeResult,
    DatabaseStats,
    PurgePreview,
    SchemaValidation,
    REQUIRED_TABLES,
    REQUIRED_SERVER_COLUMNS,
    REQUIRED_SHARE_ACCESS_COLUMNS,
    REQUIRED_FILE_MANIFEST_COLUMNS,
)
from shared.db_migrations import run_migrations


# Minimal schema for test databases
MINIMAL_SCHEMA = """
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT DEFAULT 'smbseek',
    scan_type TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    status TEXT DEFAULT 'running',
    total_targets INTEGER DEFAULT 0,
    successful_targets INTEGER DEFAULT 0,
    failed_targets INTEGER DEFAULT 0,
    notes TEXT
);

CREATE TABLE smb_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT NOT NULL UNIQUE,
    country TEXT,
    country_code TEXT,
    auth_method TEXT,
    shodan_data TEXT,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    scan_count INTEGER DEFAULT 1,
    status TEXT DEFAULT 'active',
    notes TEXT,
    updated_at DATETIME
);

CREATE TABLE share_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    accessible BOOLEAN DEFAULT FALSE,
    auth_status TEXT,
    permissions TEXT,
    share_type TEXT,
    share_comment TEXT,
    test_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    access_details TEXT,
    error_message TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE share_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL,
    share_name TEXT NOT NULL,
    username TEXT,
    password TEXT,
    source TEXT DEFAULT 'pry',
    session_id INTEGER,
    last_verified_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX idx_share_credentials_server_share_source
    ON share_credentials(server_id, share_name, source);

CREATE TABLE file_manifests (
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
    discovery_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE vulnerabilities (
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
    discovery_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'open',
    notes TEXT,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

CREATE TABLE failure_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    ip_address TEXT NOT NULL,
    failure_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    failure_type TEXT,
    failure_reason TEXT,
    shodan_data TEXT,
    analysis_results TEXT,
    retry_count INTEGER DEFAULT 0,
    last_retry_timestamp DATETIME,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE SET NULL
);

CREATE TABLE host_user_flags (
    server_id INTEGER PRIMARY KEY,
    favorite BOOLEAN DEFAULT 0,
    avoid BOOLEAN DEFAULT 0,
    notes TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

CREATE TABLE host_probe_cache (
    server_id INTEGER PRIMARY KEY,
    status TEXT DEFAULT 'unprobed',
    last_probe_at DATETIME,
    indicator_matches INTEGER DEFAULT 0,
    indicator_samples TEXT,
    snapshot_path TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);
"""


@pytest.fixture
def temp_db():
    """Create a temporary database with full schema."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def populated_db(temp_db):
    """Create a database with sample data."""
    conn = sqlite3.connect(temp_db)
    conn.execute("PRAGMA foreign_keys = ON")

    # Add a scan session
    conn.execute("""
        INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
        VALUES ('smbseek', 'discover', 'completed', 10)
    """)

    # Add some servers
    servers = [
        ('192.168.1.1', 'United States', 'US', 'anonymous', '2024-01-15', '2024-02-01'),
        ('192.168.1.2', 'United Kingdom', 'GB', 'guest', '2024-01-10', '2024-01-20'),
        ('192.168.1.3', 'Germany', 'DE', 'anonymous', '2024-01-01', '2024-01-05'),
    ]
    for ip, country, code, auth, first, last in servers:
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ip, country, code, auth, first, last))

    # Add some shares
    conn.execute("""
        INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
        VALUES (1, 1, 'Documents', 1, '2024-02-01'),
               (1, 1, 'Public', 1, '2024-02-01'),
               (2, 1, 'Users', 0, '2024-01-20')
    """)

    # Add a vulnerability
    conn.execute("""
        INSERT INTO vulnerabilities (server_id, session_id, vuln_type, severity, title)
        VALUES (1, 1, 'weak_auth', 'high', 'Anonymous access enabled')
    """)

    # Add file manifest
    conn.execute("""
        INSERT INTO file_manifests (server_id, session_id, share_name, file_path, file_name)
        VALUES (1, 1, 'Documents', '/secret.txt', 'secret.txt')
    """)

    # Add user flags
    conn.execute("""
        INSERT INTO host_user_flags (server_id, favorite, notes)
        VALUES (1, 1, 'Important server')
    """)

    # Add probe cache
    conn.execute("""
        INSERT INTO host_probe_cache (server_id, status)
        VALUES (1, 'probed')
    """)

    conn.commit()
    conn.close()

    return temp_db


@pytest.fixture
def external_db():
    """Create an external database with different data for merge testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.executescript(MINIMAL_SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")

    # Add a scan session
    conn.execute("""
        INSERT INTO scan_sessions (tool_name, scan_type, status, total_targets)
        VALUES ('smbseek', 'discover', 'completed', 5)
    """)

    # Add servers - some overlapping, some new
    servers = [
        ('192.168.1.1', 'United States', 'US', 'guest', '2024-01-20', '2024-03-01'),  # Overlap, newer
        ('192.168.1.2', 'United Kingdom', 'GB', 'guest', '2024-01-10', '2024-01-15'),  # Overlap, older
        ('192.168.1.4', 'France', 'FR', 'anonymous', '2024-02-01', '2024-02-15'),  # New
    ]
    for ip, country, code, auth, first, last in servers:
        conn.execute("""
            INSERT INTO smb_servers (ip_address, country, country_code, auth_method, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ip, country, code, auth, first, last))

    # Add shares for new server
    conn.execute("""
        INSERT INTO share_access (server_id, session_id, share_name, accessible, test_timestamp)
        VALUES (3, 1, 'Archive', 1, '2024-02-15')
    """)

    conn.commit()
    conn.close()

    yield db_path

    try:
        os.unlink(db_path)
    except Exception:
        pass


