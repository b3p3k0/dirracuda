#!/usr/bin/env python3
"""
Dirracuda Database Enhancement: Add Share Summary View

This script safely adds a new database view (v_host_share_summary) that provides
complete share discovery and accessibility information per host. This enhancement
enables easy querying of both total discovered shares and accessible shares.

The script is safe to run on existing databases and makes no schema changes,
only adds a view and an index for performance optimization.

Usage:
    python3 tools/add_share_summary_view.py [--database PATH] [--dry-run]
"""

import sqlite3
import argparse
import os
import sys
from pathlib import Path

# Add shared directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
from config import load_config
try:
    from shared.db_path_resolution import resolve_database_path
except ImportError:
    from db_path_resolution import resolve_database_path


def resolve_cli_database_path(explicit_path: str | None) -> str:
    """Resolve DB path using backend-rooted semantics and legacy auto-detect."""
    backend_root = Path(__file__).resolve().parents[1]
    persisted_paths = []

    if not explicit_path:
        try:
            config = load_config()
            persisted_paths.append(config.get_database_path())
        except Exception:
            persisted_paths = []

    return str(
        resolve_database_path(
            backend_path=backend_root,
            cli_database_path=explicit_path,
            persisted_paths=persisted_paths,
        )
    )


def check_database_compatibility(conn: sqlite3.Connection) -> bool:
    """
    Check if database has required tables and structure.
    
    Args:
        conn: SQLite connection
        
    Returns:
        True if database is compatible, False otherwise
    """
    required_tables = ['smb_servers', 'share_access']
    required_columns = {
        'smb_servers': ['id', 'ip_address', 'country', 'auth_method', 'first_seen', 'last_seen'],
        'share_access': ['id', 'server_id', 'share_name', 'accessible', 'test_timestamp']
    }
    
    try:
        cursor = conn.cursor()
        
        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        
        for table in required_tables:
            if table not in existing_tables:
                print(f"❌ Required table '{table}' not found in database")
                return False
        
        # Check required columns exist
        for table, columns in required_columns.items():
            cursor.execute(f"PRAGMA table_info({table})")
            existing_columns = {row[1] for row in cursor.fetchall()}
            
            for column in columns:
                if column not in existing_columns:
                    print(f"❌ Required column '{column}' not found in table '{table}'")
                    return False
        
        print("✅ Database structure is compatible")
        return True
        
    except Exception as e:
        print(f"❌ Error checking database compatibility: {e}")
        return False


def check_existing_view(conn: sqlite3.Connection) -> bool:
    """
    Check if the view already exists.
    
    Args:
        conn: SQLite connection
        
    Returns:
        True if view already exists, False otherwise
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='view' AND name='v_host_share_summary'
        """)
        exists = cursor.fetchone() is not None
        
        if exists:
            print("ℹ️  View 'v_host_share_summary' already exists")
        else:
            print("✅ View 'v_host_share_summary' does not exist - safe to create")
            
        return exists
        
    except Exception as e:
        print(f"❌ Error checking existing view: {e}")
        return True  # Assume exists to be safe


def get_sample_data_stats(conn: sqlite3.Connection) -> dict:
    """
    Get sample statistics from existing data to validate view will work.
    
    Args:
        conn: SQLite connection
        
    Returns:
        Dictionary with data statistics
    """
    try:
        cursor = conn.cursor()
        
        # Count servers with share data
        cursor.execute("""
            SELECT COUNT(DISTINCT s.ip_address) 
            FROM smb_servers s
            INNER JOIN share_access sa ON s.id = sa.server_id
        """)
        servers_with_shares = cursor.fetchone()[0]
        
        # Count total share records
        cursor.execute("SELECT COUNT(*) FROM share_access")
        total_share_records = cursor.fetchone()[0]
        
        # Count accessible shares
        cursor.execute("SELECT COUNT(*) FROM share_access WHERE accessible = 1")
        accessible_shares = cursor.fetchone()[0]
        
        return {
            'servers_with_shares': servers_with_shares,
            'total_share_records': total_share_records,
            'accessible_shares': accessible_shares
        }
        
    except Exception as e:
        print(f"⚠️ Error getting sample data stats: {e}")
        return {}


def apply_view_enhancement(conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    """
    Apply the share summary view enhancement to the database.
    
    Args:
        conn: SQLite connection
        dry_run: If True, only show what would be done
        
    Returns:
        True if successful, False otherwise
    """
    script_dir = os.path.dirname(__file__)
    sql_file_path = os.path.join(script_dir, 'add_share_summary_view.sql')
    
    if not os.path.exists(sql_file_path):
        print(f"❌ SQL file not found: {sql_file_path}")
        return False
    
    try:
        with open(sql_file_path, 'r') as f:
            sql_content = f.read()
        
        if dry_run:
            print("\n🔍 DRY RUN - SQL that would be executed:")
            print("-" * 50)
            print(sql_content)
            print("-" * 50)
            print("✅ Dry run completed - no changes made")
            return True
        
        print("Applying database enhancement...")
        cursor = conn.cursor()
        cursor.executescript(sql_content)
        conn.commit()
        
        print("✅ Database view and index created successfully")
        return True
        
    except Exception as e:
        print(f"❌ Error applying enhancement: {e}")
        return False


def test_view_functionality(conn: sqlite3.Connection) -> bool:
    """
    Test that the new view works correctly.
    
    Args:
        conn: SQLite connection
        
    Returns:
        True if view works correctly, False otherwise
    """
    try:
        cursor = conn.cursor()
        
        # Test basic view query
        cursor.execute("SELECT COUNT(*) FROM v_host_share_summary")
        view_count = cursor.fetchone()[0]
        
        # Test specific functionality
        cursor.execute("""
            SELECT ip_address, total_shares_discovered, accessible_shares_count,
                   all_shares_list, accessible_shares_list
            FROM v_host_share_summary
            LIMIT 1
        """)
        sample_row = cursor.fetchone()
        
        if sample_row:
            ip, total, accessible, all_shares, acc_shares = sample_row
            print(f"✅ View test successful - sample data: {ip} has {total} total shares, {accessible} accessible")
            if all_shares:
                shares_list = all_shares.split(',')
                print(f"   Sample shares: {shares_list[:3]}{'...' if len(shares_list) > 3 else ''}")
        else:
            print("⚠️  View created but no data returned (this is normal for empty databases)")
        
        print(f"✅ View query successful - {view_count} host records available")
        return True
        
    except Exception as e:
        print(f"❌ Error testing view functionality: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Add share summary view to Dirracuda database")
    parser.add_argument(
        "--database",
        type=str,
        help="Path to database file (default: auto-detect dirracuda.db then smbseek.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--force", action="store_true", help="Proceed even if view already exists")
    args = parser.parse_args()
    
    # Determine database path
    db_path = resolve_cli_database_path(args.database)
    
    print(f"Dirracuda Share Summary View Enhancement")
    print(f"Database: {os.path.abspath(db_path)}")
    
    if not os.path.exists(db_path):
        print(f"❌ Database file not found: {db_path}")
        print("Make sure you've run Dirracuda at least once to create the database.")
        return 1
    
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Check database compatibility
            if not check_database_compatibility(conn):
                print("❌ Database is not compatible with this enhancement")
                return 1
            
            # Check if enhancement already exists
            view_exists = check_existing_view(conn)
            if view_exists and not args.force:
                print("Enhancement already applied. Use --force to recreate.")
                return 0
            
            # Get current data stats
            stats = get_sample_data_stats(conn)
            if stats:
                print(f"📊 Current database stats:")
                print(f"   • Servers with share data: {stats['servers_with_shares']}")
                print(f"   • Total share records: {stats['total_share_records']}")
                print(f"   • Accessible shares: {stats['accessible_shares']}")
            
            # Apply enhancement
            if apply_view_enhancement(conn, dry_run=args.dry_run):
                if not args.dry_run:
                    # Test the new view
                    if test_view_functionality(conn):
                        print("\n✅ Share summary view enhancement completed successfully!")
                        print("GUI developers can now use the new methods in shared/database.py")
                    else:
                        print("⚠️  View created but testing failed - please verify manually")
                        return 1
                return 0
            else:
                return 1
                
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
