#!/usr/bin/env python3
"""
Generate bogan test databases for Dirracuda import testing.

Creates two SQLite databases with synthetic data:
- smbseek_bogan_sm.db: 25 servers (small dataset)
- smbseek_bogan_lg.db: 2500 servers (large dataset)

All IPs start with 666.x.y.z for easy identification of test data.
Share names follow BOGAN_TEST_SM or BOGAN_TEST_LG patterns.
"""

import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
SMALL_COUNT = 25
LARGE_COUNT = 2500
SCRIPT_DIR = Path(__file__).parent
SCHEMA_PATH = SCRIPT_DIR.parent.parent / "tools" / "db_schema.sql"

# Aussie-themed data for easter eggs
AUSSIE_NOTES = [
    "Fair dinkum open share, mate",
    "She'll be right",
    "No wukkas, help yourself",
    "Strewth, wide open this one",
    "Bonzer find, this server",
    "Crikey, anonymous access enabled",
    "Too easy, mate",
    "Chuck a sickie and check this out",
    "Good as gold",
    "Bloody ripper of a share",
    "Stone the crows, no auth needed",
    "Bob's your uncle, guest access works",
    "Flat out like a lizard drinking",
    "This arvo's discovery",
    "Reckon this one's a goer",
]

SHARE_COMMENTS = [
    "No wukkas, help yourself",
    "Public files, mate",
    "Chucked all the docs here",
    "Company stuff, she'll be right",
    "Archives from yonks ago",
    "IT dept backup folder",
    "Shared resources for the team",
    "General purpose storage",
    "Marketing collateral",
    "Old project files",
]

VULN_DESCRIPTIONS = [
    "SMB signing is disabled - bit suss, that",
    "Anonymous access enabled - fair dinkum security risk",
    "Weak authentication detected - dodgy as",
    "Guest access allows file enumeration - not ideal, mate",
    "Null session permitted - risky business",
    "Legacy SMBv1 detected - old as the hills",
]

FILE_NAMES_THEMATIC = [
    ("readme.txt", ".txt", "text/plain"),
    ("config_backup.txt", ".txt", "text/plain"),
    ("straya_day_plans.doc", ".doc", "application/msword"),
    ("team_roster.xls", ".xls", "application/vnd.ms-excel"),
    ("quarterly_report.pdf", ".pdf", "application/pdf"),
    ("meeting_notes.doc", ".doc", "application/msword"),
    ("passwords_backup.txt", ".txt", "text/plain"),  # Classic
    ("network_diagram.pdf", ".pdf", "application/pdf"),
    ("employee_list.xls", ".xls", "application/vnd.ms-excel"),
    ("project_timeline.doc", ".doc", "application/msword"),
    ("budget_2025.xls", ".xls", "application/vnd.ms-excel"),
    ("policy_draft.doc", ".doc", "application/msword"),
]

# Country weights (70% AU because it's bogan)
COUNTRIES = [
    ("Australia", "AU", 70),
    ("United States", "US", 10),
    ("United Kingdom", "GB", 8),
    ("Germany", "DE", 6),
    ("New Zealand", "NZ", 6),
]

AUTH_METHODS = ["anonymous", "guest", "authenticated"]
AUTH_WEIGHTS = [40, 35, 25]

SHARE_SUFFIXES = ["", "_PUB", "_DOCS", "_BACKUP", "_SHARED"]


def weighted_choice(items_with_weights):
    """Select item based on weights."""
    items = [x[:-1] if isinstance(x, tuple) and len(x) == 3 else x for x in items_with_weights]
    weights = [x[-1] for x in items_with_weights]
    return random.choices(items, weights=weights, k=1)[0]


def generate_ip(index: int) -> str:
    """Generate a 666.x.y.z IP address from index."""
    # Distribute across 666.0.0.0 to 666.255.255.255
    octet2 = (index // 65536) % 256
    octet3 = (index // 256) % 256
    octet4 = index % 256
    return f"666.{octet2}.{octet3}.{octet4}"


def random_timestamp(days_back_max: int = 90) -> str:
    """Generate a random timestamp within the last N days."""
    now = datetime.now()
    delta = timedelta(
        days=random.randint(0, days_back_max),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return (now - delta).strftime("%Y-%m-%d %H:%M:%S")


def create_database(db_path: Path, server_count: int, size_tag: str):
    """Create and populate a test database."""
    print(f"Creating {db_path.name} with {server_count} servers...")

    # Remove existing database
    if db_path.exists():
        db_path.unlink()

    # Read and apply schema
    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema_sql)
    cursor = conn.cursor()

    # Create a scan session for this import
    cursor.execute("""
        INSERT INTO scan_sessions (
            tool_name, scan_type, status, notes, timestamp, started_at, completed_at,
            total_targets, successful_targets, country_filter
        ) VALUES (
            'bogan_generator', 'synthetic', 'completed',
            'Bogan test data generation', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP, ?, ?, 'AU,US,GB,DE,NZ'
        )
    """, (server_count, server_count))
    session_id = cursor.lastrowid

    # Track accessible shares for file manifests
    accessible_shares = []

    # Generate servers
    for i in range(server_count):
        ip = generate_ip(i + 1)  # Start from 666.0.0.1
        country_data = weighted_choice(COUNTRIES)
        country, country_code = country_data
        auth_method = random.choices(AUTH_METHODS, weights=AUTH_WEIGHTS, k=1)[0]

        first_seen = random_timestamp(90)
        last_seen = random_timestamp(30)

        # Ensure last_seen >= first_seen
        if last_seen < first_seen:
            first_seen, last_seen = last_seen, first_seen

        note = random.choice(AUSSIE_NOTES) if random.random() < 0.3 else None

        cursor.execute("""
            INSERT INTO smb_servers (
                ip_address, country, country_code, auth_method,
                first_seen, last_seen, scan_count, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """, (ip, country, country_code, auth_method, first_seen, last_seen,
              random.randint(1, 5), note))
        server_id = cursor.lastrowid

        # Generate shares (1-3 per server)
        num_shares = random.randint(1, 3)
        for j in range(num_shares):
            suffix = SHARE_SUFFIXES[j] if j < len(SHARE_SUFFIXES) else f"_{j}"
            share_name = f"BOGAN_TEST_{size_tag}{suffix}"
            accessible = random.random() < 0.7  # 70% accessible
            auth_status = auth_method if accessible else "denied"
            permissions = "read" if accessible else None
            share_comment = random.choice(SHARE_COMMENTS) if random.random() < 0.5 else None

            cursor.execute("""
                INSERT INTO share_access (
                    server_id, session_id, share_name, accessible, auth_status,
                    permissions, share_type, share_comment, test_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, 'disk', ?, ?)
            """, (server_id, session_id, share_name, accessible, auth_status,
                  permissions, share_comment, last_seen))

            if accessible:
                share_access_id = cursor.lastrowid
                accessible_shares.append((server_id, session_id, share_name, share_access_id))

        # Generate vulnerabilities (~10% of servers)
        if random.random() < 0.10:
            num_vulns = random.randint(1, 2)
            vuln_types = random.sample(
                ["weak_auth", "smb_signing_disabled", "anonymous_access", "legacy_protocol"],
                k=min(num_vulns, 4)
            )
            severities = ["low", "medium", "high"]
            severity_weights = [40, 40, 20]

            for vuln_type in vuln_types:
                severity = random.choices(severities, weights=severity_weights, k=1)[0]
                title = vuln_type.replace("_", " ").title()
                description = random.choice(VULN_DESCRIPTIONS)

                cursor.execute("""
                    INSERT INTO vulnerabilities (
                        server_id, session_id, vuln_type, severity, title,
                        description, discovery_timestamp, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                """, (server_id, session_id, vuln_type, severity, title,
                      description, last_seen))

        # Progress update for large datasets
        if server_count > 100 and (i + 1) % 500 == 0:
            print(f"  Generated {i + 1}/{server_count} servers...")
            conn.commit()

    # Generate file manifests (~20% of accessible shares)
    print(f"  Generating file manifests for {len(accessible_shares)} accessible shares...")
    for server_id, session_id, share_name, _ in accessible_shares:
        if random.random() < 0.20:
            num_files = random.randint(1, 5)
            selected_files = random.sample(FILE_NAMES_THEMATIC, k=min(num_files, len(FILE_NAMES_THEMATIC)))

            for file_name, file_ext, mime_type in selected_files:
                file_path = f"/{share_name}/{file_name}"
                file_size = random.randint(1024, 1024 * 1024 * 10)  # 1KB to 10MB
                is_sensitive = "password" in file_name.lower()
                modified = random_timestamp(60)

                cursor.execute("""
                    INSERT INTO file_manifests (
                        server_id, session_id, share_name, file_path, file_name,
                        file_size, file_type, file_extension, mime_type,
                        last_modified, is_ransomware_indicator, is_sensitive,
                        discovery_timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (server_id, session_id, share_name, file_path, file_name,
                      file_size, "document", file_ext, mime_type, modified,
                      is_sensitive, modified))

    conn.commit()

    # Get stats
    cursor.execute("SELECT COUNT(*) FROM smb_servers")
    server_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM share_access")
    share_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM vulnerabilities")
    vuln_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM file_manifests")
    file_count = cursor.fetchone()[0]

    conn.close()

    print(f"  Created: {server_count} servers, {share_count} shares, "
          f"{vuln_count} vulnerabilities, {file_count} file manifests")
    print(f"  Database size: {db_path.stat().st_size / 1024:.1f} KB")


def main():
    """Generate both test databases."""
    print("=" * 60)
    print("Bogan Test Database Generator")
    print("=" * 60)

    if not SCHEMA_PATH.exists():
        print(f"ERROR: Schema file not found: {SCHEMA_PATH}")
        return 1

    # Generate small database
    small_db = SCRIPT_DIR / "smbseek_bogan_sm.db"
    create_database(small_db, SMALL_COUNT, "SM")
    print()

    # Generate large database
    large_db = SCRIPT_DIR / "smbseek_bogan_lg.db"
    create_database(large_db, LARGE_COUNT, "LG")
    print()

    print("=" * 60)
    print("Generation complete!")
    print(f"  Small: {small_db}")
    print(f"  Large: {large_db}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit(main())
