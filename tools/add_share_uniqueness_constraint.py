#!/usr/bin/env python3
"""
Dirracuda Database Migration: Add Share Uniqueness Constraint

This script adds a UNIQUE constraint to the share_access table to prevent
duplicate share entries per server. This migration is part of fixing the
share counting discrepancies issue.

Usage:
    python3 tools/add_share_uniqueness_constraint.py [--database PATH] [--dry-run]
"""

import sqlite3
import sys
import os
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import load_config


def check_existing_constraint(conn: sqlite3.Connection) -> bool:
    """
    Check if the UNIQUE constraint already exists.
    
    Args:
        conn: SQLite connection
        
    Returns:
        True if constraint exists, False otherwise
    """
    cursor = conn.execute("""
        SELECT sql FROM sqlite_master 
        WHERE type='table' AND name='share_access'
    """)
    
    table_sql = cursor.fetchone()
    if table_sql:
        sql = table_sql[0].upper()
        # Check if UNIQUE constraint on server_id, share_name exists
        return 'UNIQUE' in sql and 'SERVER_ID' in sql and 'SHARE_NAME' in sql
    
    return False


def find_duplicate_shares(conn: sqlite3.Connection) -> list:
    """
    Find servers with duplicate share entries.
    
    Args:
        conn: SQLite connection
        
    Returns:
        List of tuples (server_id, share_name, count)
    """
    cursor = conn.execute("""
        SELECT server_id, share_name, COUNT(*) as duplicate_count
        FROM share_access
        GROUP BY server_id, share_name
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, server_id, share_name
    """)
    
    return cursor.fetchall()


def backup_share_access_table(conn: sqlite3.Connection) -> None:
    """
    Create a backup of the share_access table before migration.
    
    Args:
        conn: SQLite connection
    """
    print("Creating backup of share_access table...")
    
    # Create backup table (drop if exists first)
    conn.execute("DROP TABLE IF EXISTS share_access_backup")
    conn.execute("""
        CREATE TABLE share_access_backup AS 
        SELECT * FROM share_access
    """)
    
    backup_count = conn.execute("SELECT COUNT(*) FROM share_access_backup").fetchone()[0]
    print(f"✅ Backup created with {backup_count:,} records")


def add_uniqueness_constraint(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Add UNIQUE constraint to share_access table.
    
    Args:
        conn: SQLite connection
        dry_run: If True, only show what would be done
        
    Returns:
        True if successful, False otherwise
    """
    if dry_run:
        print("\n🔍 DRY RUN: Would add UNIQUE constraint to share_access table")
        print("Steps that would be performed:")
        print("1. Create backup table (share_access_backup)")
        print("2. Create new table with UNIQUE constraint")
        print("3. Copy data from old table to new table")
        print("4. Drop old table and rename new table")
        return True
    
    try:
        print("Adding UNIQUE constraint to share_access table...")
        
        # Create new table with UNIQUE constraint
        conn.execute("""
            CREATE TABLE share_access_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                share_name VARCHAR(255) NOT NULL,
                accessible BOOLEAN NOT NULL DEFAULT FALSE,
                permissions TEXT,
                share_type VARCHAR(50),
                share_comment TEXT,
                test_timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                access_details TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, share_name),
                FOREIGN KEY (server_id) REFERENCES smb_servers(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
            )
        """)
        
        # Copy data from old table, handling duplicates by keeping latest session
        print("Copying data and resolving duplicates...")
        conn.execute("""
            INSERT INTO share_access_new (
                id, server_id, session_id, share_name, accessible, permissions,
                share_type, share_comment, test_timestamp, access_details,
                error_message, created_at
            )
            SELECT 
                sa.id, sa.server_id, sa.session_id, sa.share_name, sa.accessible,
                sa.permissions, sa.share_type, sa.share_comment, sa.test_timestamp,
                sa.access_details, sa.error_message, sa.created_at
            FROM share_access sa
            INNER JOIN (
                SELECT server_id, share_name, MAX(session_id) as max_session_id
                FROM share_access
                GROUP BY server_id, share_name
            ) latest ON sa.server_id = latest.server_id 
                   AND sa.share_name = latest.share_name 
                   AND sa.session_id = latest.max_session_id
        """)
        
        # Get record counts
        old_count = conn.execute("SELECT COUNT(*) FROM share_access").fetchone()[0]
        new_count = conn.execute("SELECT COUNT(*) FROM share_access_new").fetchone()[0]
        
        # Drop views that reference share_access table before dropping the table
        print("Temporarily dropping views that reference share_access...")
        views_to_recreate = []
        
        # Get views that reference share_access
        cursor = conn.execute("""
            SELECT name, sql FROM sqlite_master 
            WHERE type='view' AND sql LIKE '%share_access%'
        """)
        
        for view_row in cursor.fetchall():
            views_to_recreate.append((view_row[0], view_row[1]))
            conn.execute(f"DROP VIEW IF EXISTS {view_row[0]}")
        
        # Drop old table and rename new one
        conn.execute("DROP TABLE share_access")
        conn.execute("ALTER TABLE share_access_new RENAME TO share_access")
        
        # Recreate views
        if views_to_recreate:
            print(f"Recreating {len(views_to_recreate)} views...")
            for view_name, view_sql in views_to_recreate:
                try:
                    conn.execute(view_sql)
                    print(f"  ✅ Recreated view: {view_name}")
                except Exception as e:
                    print(f"  ⚠️  Warning: Failed to recreate view {view_name}: {e}")
                    # Continue anyway - views can be manually recreated if needed
        
        # Recreate indexes
        conn.execute("CREATE INDEX idx_share_access_server ON share_access(server_id)")
        conn.execute("CREATE INDEX idx_share_access_session ON share_access(session_id)")
        conn.execute("CREATE INDEX idx_share_access_accessible ON share_access(accessible)")
        
        print(f"✅ Migration completed successfully")
        print(f"   Records before: {old_count:,}")
        print(f"   Records after: {new_count:,}")
        print(f"   Duplicates removed: {old_count - new_count:,}")
        
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        # Try to rollback
        try:
            conn.execute("DROP TABLE IF EXISTS share_access_new")
            print("Cleaned up temporary table")
        except:
            pass
        return False


def validate_constraint(conn: sqlite3.Connection) -> bool:
    """
    Validate that the UNIQUE constraint is working.
    
    Args:
        conn: SQLite connection
        
    Returns:
        True if constraint is working, False otherwise
    """
    print("Validating UNIQUE constraint...")
    
    try:
        # Try to insert a duplicate - should fail
        conn.execute("""
            INSERT INTO share_access (server_id, session_id, share_name, accessible)
            VALUES (999, 999, 'test_share', 0)
        """)
        
        conn.execute("""
            INSERT INTO share_access (server_id, session_id, share_name, accessible)
            VALUES (999, 999, 'test_share', 0)
        """)
        
        # If we get here, constraint is not working
        print("❌ UNIQUE constraint validation failed - duplicate insert succeeded")
        # Clean up test data
        conn.execute("DELETE FROM share_access WHERE server_id = 999")
        return False
        
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            print("✅ UNIQUE constraint is working correctly")
            # Clean up test data
            conn.execute("DELETE FROM share_access WHERE server_id = 999")
            return True
        else:
            print(f"❌ Unexpected integrity error: {e}")
            return False
    
    except Exception as e:
        print(f"❌ Constraint validation error: {e}")
        return False


def main():
    """Main migration script."""
    parser = argparse.ArgumentParser(description="Add UNIQUE constraint to share_access table")
    parser.add_argument("--database", type=str, help="Path to database file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    try:
        # Get database path
        if args.database:
            db_path = args.database
        else:
            # Use default from config
            config = load_config()
            db_path = config.get_database_path()
        
        print(f"🗄️  Dirracuda Database Migration: Add Share Uniqueness Constraint")
        print(f"Database: {db_path}")
        
        if not os.path.exists(db_path):
            print(f"❌ Database file not found: {db_path}")
            return 1
        
        # Connect to database
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        try:
            # Check if constraint already exists
            if check_existing_constraint(conn):
                print("✅ UNIQUE constraint already exists - no migration needed")
                return 0
            
            # Find duplicate shares
            duplicates = find_duplicate_shares(conn)
            if duplicates:
                print(f"\n⚠️  Found {len(duplicates)} server-share combinations with duplicates:")
                for i, (server_id, share_name, count) in enumerate(duplicates[:10]):  # Show first 10
                    print(f"   {i+1:2d}. Server {server_id}: '{share_name}' ({count} duplicates)")
                if len(duplicates) > 10:
                    print(f"   ... and {len(duplicates) - 10} more")
                print()
            else:
                print("✅ No duplicate shares found")
            
            if not args.dry_run:
                # Create backup
                backup_share_access_table(conn)
                
                # Add constraint
                if add_uniqueness_constraint(conn, dry_run=False):
                    # Validate constraint works
                    if validate_constraint(conn):
                        print("\n✅ Migration completed successfully!")
                        print("The share_access table now prevents duplicate shares per server.")
                    else:
                        print("\n❌ Migration completed but constraint validation failed")
                        return 1
                else:
                    print("\n❌ Migration failed")
                    return 1
            else:
                add_uniqueness_constraint(conn, dry_run=True)
            
        finally:
            conn.close()
        
        return 0
        
    except Exception as e:
        print(f"❌ Migration error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())