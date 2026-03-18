-- SMBSeek SQLite Database Schema
-- Central database for SMB scanning and analysis data

-- Drop existing tables if they exist (for fresh installations)
-- Child tables must drop before parents to avoid FK constraint failures.
DROP TABLE IF EXISTS ftp_probe_cache;
DROP TABLE IF EXISTS ftp_user_flags;
DROP TABLE IF EXISTS ftp_access;
DROP TABLE IF EXISTS ftp_servers;
DROP TABLE IF EXISTS file_manifests;
DROP TABLE IF EXISTS vulnerabilities;
DROP TABLE IF EXISTS share_access;
DROP TABLE IF EXISTS failure_logs;
DROP TABLE IF EXISTS smb_servers;
DROP TABLE IF EXISTS scan_sessions;

-- Core scan session tracking
CREATE TABLE scan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT DEFAULT 'smbseek',
    scan_type TEXT NOT NULL,
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
);

-- Central SMB server registry
CREATE TABLE smb_servers (
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
);

-- SMB share accessibility results
CREATE TABLE share_access (
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
);

-- Per-share credentials discovered (e.g., via Pry)
CREATE TABLE share_credentials (
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
);
CREATE UNIQUE INDEX idx_share_credentials_server_share_source
    ON share_credentials(server_id, share_name, source);

-- GUI user flags (favorites/avoid/notes)
CREATE TABLE host_user_flags (
    server_id INTEGER PRIMARY KEY,
    favorite BOOLEAN DEFAULT 0,
    avoid BOOLEAN DEFAULT 0,
    notes TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE
);

-- GUI probe cache (latest probe status/indicators)
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

-- File discovery and manifest records
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
    discovery_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

-- Security vulnerability findings
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
    discovery_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'open',
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
);

-- Connection failure logs and analysis
CREATE TABLE failure_logs (
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
);

-- Create indexes for performance
CREATE INDEX idx_smb_servers_ip ON smb_servers(ip_address);
CREATE INDEX idx_smb_servers_country ON smb_servers(country);
CREATE INDEX idx_smb_servers_last_seen ON smb_servers(last_seen);
CREATE INDEX idx_share_access_server ON share_access(server_id);
CREATE INDEX idx_share_access_session ON share_access(session_id);
CREATE INDEX idx_file_manifests_server ON file_manifests(server_id);
CREATE INDEX idx_file_manifests_session ON file_manifests(session_id);
CREATE INDEX idx_vulnerabilities_server ON vulnerabilities(server_id);
CREATE INDEX idx_vulnerabilities_session ON vulnerabilities(session_id);
CREATE INDEX idx_failure_logs_ip ON failure_logs(ip_address);
CREATE INDEX idx_failure_logs_timestamp ON failure_logs(failure_timestamp);
CREATE INDEX idx_scan_sessions_timestamp ON scan_sessions(timestamp);
CREATE INDEX idx_scan_sessions_tool ON scan_sessions(tool_name);

-- Views
CREATE VIEW v_active_servers AS
SELECT 
    s.id,
    s.ip_address,
    s.country,
    s.auth_method,
    s.first_seen,
    s.last_seen,
    s.scan_count,
    COUNT(DISTINCT sa.share_name) AS accessible_shares_count,
    COUNT(DISTINCT fm.file_path) AS files_discovered,
    COUNT(DISTINCT v.id) AS vulnerability_count
FROM smb_servers s
LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = TRUE
LEFT JOIN file_manifests fm ON s.id = fm.server_id
LEFT JOIN vulnerabilities v ON s.id = v.server_id AND v.status = 'open'
WHERE s.status = 'active'
GROUP BY s.id;

CREATE VIEW v_vulnerability_summary AS
SELECT 
    vuln_type,
    severity,
    COUNT(*) AS count,
    COUNT(DISTINCT server_id) AS affected_servers
FROM vulnerabilities 
WHERE status = 'open'
GROUP BY vuln_type, severity
ORDER BY 
    CASE severity 
        WHEN 'critical' THEN 1
        WHEN 'high' THEN 2  
        WHEN 'medium' THEN 3
        WHEN 'low' THEN 4
        ELSE 5
    END,
    count DESC;

CREATE VIEW v_scan_statistics AS
SELECT
    tool_name,
    DATE(timestamp) AS scan_date,
    COUNT(*) AS sessions,
    SUM(total_targets) AS total_targets,
    SUM(successful_targets) AS successful_targets,
    SUM(failed_targets) AS failed_targets,
    ROUND(AVG(
        CASE WHEN total_targets > 0 THEN CAST(successful_targets AS FLOAT) / CAST(total_targets AS FLOAT)
        ELSE 0 END
    ) * 100, 2) AS success_rate
FROM scan_sessions
WHERE total_targets > 0
GROUP BY tool_name, DATE(timestamp)
ORDER BY scan_date DESC;

-- FTP sidecar tables (additive; SMB schema untouched)

-- Central FTP server registry — one row per IP address (host-centric, port is informational)
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
);

-- Per-session FTP access summary (parallel to share_access)
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
);

CREATE INDEX IF NOT EXISTS idx_ftp_servers_ip       ON ftp_servers(ip_address);
CREATE INDEX IF NOT EXISTS idx_ftp_servers_country  ON ftp_servers(country);
CREATE INDEX IF NOT EXISTS idx_ftp_servers_last_seen ON ftp_servers(last_seen);
CREATE INDEX IF NOT EXISTS idx_ftp_access_server    ON ftp_access(server_id);
CREATE INDEX IF NOT EXISTS idx_ftp_access_session   ON ftp_access(session_id);

-- FTP state tables (protocol-specific; parallel to host_user_flags / host_probe_cache)

-- Per-FTP-server user flags — favorite/avoid/notes independent from SMB flags
CREATE TABLE IF NOT EXISTS ftp_user_flags (
    server_id  INTEGER  PRIMARY KEY,
    favorite   BOOLEAN  DEFAULT 0,
    avoid      BOOLEAN  DEFAULT 0,
    notes      TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (server_id) REFERENCES ftp_servers(id) ON DELETE CASCADE
);

-- Per-FTP-server probe cache — status/indicators/extracted/rce independent from SMB probe cache
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
);

-- Protocol coexistence view — resolves has_smb / has_ftp / both per IP
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
GROUP BY ip_address;
