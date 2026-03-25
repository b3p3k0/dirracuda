#!/usr/bin/env python3
"""
Dirracuda Database Cleanup: Remove Duplicate Share Entries

This script performs a one-time cleanup of the Dirracuda database to remove
duplicate share entries per server, keeping only the most recent scan results.

This addresses the share counting discrepancies where servers were rescanned
multiple times, creating duplicate share records that inflated the counts
displayed in dashboards and reports.

Usage:
    python3 tools/cleanup_duplicate_shares.py [--database PATH] [--dry-run] [--verbose]
"""

import sqlite3
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config import load_config


class DatabaseCleanup:
    """Handles cleanup of duplicate share entries."""
    
    def __init__(self, db_path: str, verbose: bool = False):
        """
        Initialize cleanup manager.
        
        Args:
            db_path: Path to SQLite database
            verbose: Enable verbose output
        """
        self.db_path = db_path
        self.verbose = verbose
        self.conn = None
        self.stats = {
            'servers_analyzed': 0,
            'servers_with_duplicates': 0,
            'total_share_records_before': 0,
            'total_share_records_after': 0,
            'duplicate_records_removed': 0,
            'cleanup_start_time': None,
            'cleanup_end_time': None
        }
    
    def connect(self) -> bool:
        """
        Connect to database.
        
        Returns:
            True if connection successful
        """
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            return True
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            return False
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
    
    def analyze_duplicates(self) -> dict:
        """
        Analyze the current duplicate situation.
        
        Returns:
            Dictionary with analysis results
        """
        if self.verbose:
            print("🔍 Analyzing duplicate share entries...")
        
        analysis = {
            'total_servers': 0,
            'total_share_records': 0,
            'duplicate_combinations': 0,
            'affected_servers': 0,
            'worst_offenders': [],
            'session_distribution': {}
        }
        
        # Total servers and share records
        cursor = self.conn.execute("SELECT COUNT(*) FROM smb_servers")
        analysis['total_servers'] = cursor.fetchone()[0]
        
        cursor = self.conn.execute("SELECT COUNT(*) FROM share_access")
        analysis['total_share_records'] = cursor.fetchone()[0]
        
        # Find duplicate server-share combinations
        cursor = self.conn.execute("""
            SELECT server_id, share_name, COUNT(*) as duplicate_count
            FROM share_access
            GROUP BY server_id, share_name
            HAVING COUNT(*) > 1
            ORDER BY duplicate_count DESC
        """)
        
        duplicates = cursor.fetchall()
        analysis['duplicate_combinations'] = len(duplicates)
        analysis['affected_servers'] = len(set(row[0] for row in duplicates))
        
        # Top 10 worst offenders
        analysis['worst_offenders'] = duplicates[:10]
        
        # Session distribution
        cursor = self.conn.execute("""
            SELECT session_id, COUNT(*) as record_count
            FROM share_access
            GROUP BY session_id
            ORDER BY record_count DESC
        """)
        
        analysis['session_distribution'] = dict(cursor.fetchall())
        
        return analysis
    
    def get_server_details(self, server_id: int) -> dict:
        """
        Get server details for reporting.
        
        Args:
            server_id: Server ID to look up
            
        Returns:
            Dictionary with server details
        """
        cursor = self.conn.execute("""
            SELECT ip_address, country, auth_method, first_seen, last_seen, scan_count
            FROM smb_servers
            WHERE id = ?
        """, (server_id,))
        
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            return {'ip_address': f'Unknown (ID: {server_id})'}
    
    def create_cleanup_backup(self) -> bool:
        """
        Create backup tables before cleanup.
        
        Returns:
            True if backup successful
        """
        try:
            if self.verbose:
                print("📦 Creating backup tables...")
            
            # Create backup of share_access table
            self.conn.execute("DROP TABLE IF EXISTS share_access_cleanup_backup")
            self.conn.execute("""
                CREATE TABLE share_access_cleanup_backup AS 
                SELECT * FROM share_access
            """)
            
            backup_count = self.conn.execute(
                "SELECT COUNT(*) FROM share_access_cleanup_backup"
            ).fetchone()[0]
            
            if self.verbose:
                print(f"✅ Backup created: {backup_count:,} records saved")
            
            return True
            
        except Exception as e:
            print(f"❌ Backup creation failed: {e}")
            return False
    
    def identify_records_to_keep(self) -> list:
        """
        Identify which share records to keep (most recent per server-share).
        
        Returns:
            List of record IDs to keep
        """
        if self.verbose:
            print("🎯 Identifying records to keep (most recent per server-share)...")
        
        # Strategy: For each server-share combination, keep only ONE record:
        # - From the most recent session (highest session_id)
        # - If multiple records exist in same session, keep the one with highest ID (latest inserted)
        cursor = self.conn.execute("""
            SELECT sa.id
            FROM share_access sa
            INNER JOIN scan_sessions ss ON sa.session_id = ss.id
            INNER JOIN (
                SELECT 
                    sa2.server_id,
                    sa2.share_name,
                    MAX(ss2.timestamp) as latest_timestamp,
                    MAX(sa2.session_id) as latest_session_id
                FROM share_access sa2
                INNER JOIN scan_sessions ss2 ON sa2.session_id = ss2.id
                GROUP BY sa2.server_id, sa2.share_name
            ) latest ON sa.server_id = latest.server_id 
                    AND sa.share_name = latest.share_name 
                    AND ss.timestamp = latest.latest_timestamp
                    AND sa.session_id = latest.latest_session_id
            INNER JOIN (
                -- If multiple records exist in the same session for same server-share, 
                -- keep the one with highest ID (most recently inserted)
                SELECT 
                    sa3.server_id,
                    sa3.share_name,
                    sa3.session_id,
                    MAX(sa3.id) as latest_record_id
                FROM share_access sa3
                GROUP BY sa3.server_id, sa3.share_name, sa3.session_id
            ) latest_record ON sa.server_id = latest_record.server_id
                            AND sa.share_name = latest_record.share_name
                            AND sa.session_id = latest_record.session_id
                            AND sa.id = latest_record.latest_record_id
        """)
        
        records_to_keep = [row[0] for row in cursor.fetchall()]
        
        if self.verbose:
            print(f"📌 Will keep {len(records_to_keep):,} records (exactly one per server-share)")
        
        return records_to_keep
    
    def perform_cleanup(self, records_to_keep: list, dry_run: bool = False) -> bool:
        """
        Perform the actual cleanup operation.
        
        Args:
            records_to_keep: List of record IDs to preserve
            dry_run: If True, only show what would be done
            
        Returns:
            True if cleanup successful
        """
        if dry_run:
            # Calculate what would be deleted
            cursor = self.conn.execute("SELECT COUNT(*) FROM share_access")
            total_records = cursor.fetchone()[0]
            would_delete = total_records - len(records_to_keep)
            
            print(f"\n🔍 DRY RUN: Would delete {would_delete:,} duplicate records")
            print(f"   Total records: {total_records:,}")
            print(f"   Records to keep: {len(records_to_keep):,}")
            print(f"   Records to delete: {would_delete:,}")
            return True
        
        try:
            if self.verbose:
                print("🧹 Performing cleanup operation...")
            
            # Get before count
            cursor = self.conn.execute("SELECT COUNT(*) FROM share_access")
            before_count = cursor.fetchone()[0]
            
            # Delete records not in keep list
            placeholders = ','.join(['?'] * len(records_to_keep))
            delete_query = f"""
                DELETE FROM share_access 
                WHERE id NOT IN ({placeholders})
            """
            
            cursor = self.conn.execute(delete_query, records_to_keep)
            deleted_count = cursor.rowcount
            
            # Get after count
            cursor = self.conn.execute("SELECT COUNT(*) FROM share_access")
            after_count = cursor.fetchone()[0]
            
            # Update stats
            self.stats['total_share_records_before'] = before_count
            self.stats['total_share_records_after'] = after_count
            self.stats['duplicate_records_removed'] = deleted_count
            
            if self.verbose:
                print(f"✅ Cleanup completed:")
                print(f"   Records before: {before_count:,}")
                print(f"   Records after: {after_count:,}")
                print(f"   Records deleted: {deleted_count:,}")
            
            # Commit changes
            self.conn.commit()
            return True
            
        except Exception as e:
            print(f"❌ Cleanup operation failed: {e}")
            self.conn.rollback()
            return False
    
    def validate_cleanup(self) -> bool:
        """
        Validate that cleanup was successful.
        
        Returns:
            True if validation passed
        """
        if self.verbose:
            print("✅ Validating cleanup results...")
        
        try:
            # Check for remaining duplicates
            cursor = self.conn.execute("""
                SELECT server_id, share_name, COUNT(*) as count
                FROM share_access
                GROUP BY server_id, share_name
                HAVING COUNT(*) > 1
            """)
            
            remaining_duplicates = cursor.fetchall()
            
            if remaining_duplicates:
                print(f"❌ Validation failed: {len(remaining_duplicates)} duplicate combinations remain")
                for server_id, share_name, count in remaining_duplicates[:5]:
                    server_info = self.get_server_details(server_id)
                    print(f"   Server {server_info.get('ip_address', server_id)}: '{share_name}' ({count} entries)")
                return False
            
            # Verify data integrity
            cursor = self.conn.execute("""
                SELECT COUNT(*) FROM share_access sa
                LEFT JOIN smb_servers s ON sa.server_id = s.id
                WHERE s.id IS NULL
            """)
            
            orphaned_records = cursor.fetchone()[0]
            if orphaned_records > 0:
                print(f"⚠️  Warning: {orphaned_records} share records reference non-existent servers")
            
            if self.verbose:
                print("✅ Validation passed: No duplicate server-share combinations remain")
            
            return True
            
        except Exception as e:
            print(f"❌ Validation error: {e}")
            return False
    
    def print_analysis_report(self, analysis: dict):
        """
        Print detailed analysis report.
        
        Args:
            analysis: Analysis results dictionary
        """
        print(f"\n📊 DUPLICATE SHARE ANALYSIS REPORT")
        print(f"=" * 50)
        print(f"Total servers in database: {analysis['total_servers']:,}")
        print(f"Total share access records: {analysis['total_share_records']:,}")
        print(f"Duplicate server-share combinations: {analysis['duplicate_combinations']:,}")
        print(f"Servers affected by duplicates: {analysis['affected_servers']:,}")
        
        if analysis['worst_offenders']:
            print(f"\nTop servers with most duplicate shares:")
            for i, (server_id, share_name, count) in enumerate(analysis['worst_offenders'], 1):
                server_info = self.get_server_details(server_id)
                ip = server_info.get('ip_address', f'ID:{server_id}')
                print(f"  {i:2d}. {ip} - '{share_name}' ({count} duplicates)")
        
        if analysis['session_distribution']:
            print(f"\nShare records by scan session:")
            sessions = sorted(analysis['session_distribution'].items(), 
                            key=lambda x: x[1], reverse=True)[:10]
            for session_id, count in sessions:
                print(f"  Session {session_id}: {count:,} records")
        
        print(f"=" * 50)
    
    def print_final_report(self):
        """Print final cleanup report."""
        print(f"\n🎉 CLEANUP COMPLETED SUCCESSFULLY")
        print(f"=" * 50)
        print(f"Servers analyzed: {self.stats['servers_analyzed']:,}")
        print(f"Share records before cleanup: {self.stats['total_share_records_before']:,}")
        print(f"Share records after cleanup: {self.stats['total_share_records_after']:,}")
        print(f"Duplicate records removed: {self.stats['duplicate_records_removed']:,}")
        
        if self.stats['cleanup_start_time'] and self.stats['cleanup_end_time']:
            duration = self.stats['cleanup_end_time'] - self.stats['cleanup_start_time']
            print(f"Cleanup duration: {duration.total_seconds():.2f} seconds")
        
        print(f"\nDatabase is now cleaned of duplicate share entries.")
        print(f"Each server-share combination now has only the most recent scan data.")
        print(f"=" * 50)
    
    def run_cleanup(self, dry_run: bool = False) -> int:
        """
        Run the complete cleanup process.
        
        Args:
            dry_run: If True, only analyze without making changes
            
        Returns:
            Exit code (0 for success, 1 for failure)
        """
        self.stats['cleanup_start_time'] = datetime.now()
        
        try:
            # Analyze current situation
            analysis = self.analyze_duplicates()
            self.print_analysis_report(analysis)
            
            if analysis['duplicate_combinations'] == 0:
                print("\n✅ No duplicate share entries found - cleanup not needed")
                return 0
            
            if not dry_run:
                # Create backup
                if not self.create_cleanup_backup():
                    return 1
                
                # Identify records to keep
                records_to_keep = self.identify_records_to_keep()
                
                # Perform cleanup
                if not self.perform_cleanup(records_to_keep, dry_run=False):
                    return 1
                
                # Validate results
                if not self.validate_cleanup():
                    return 1
                
                self.stats['cleanup_end_time'] = datetime.now()
                self.print_final_report()
            else:
                # Dry run
                records_to_keep = self.identify_records_to_keep()
                self.perform_cleanup(records_to_keep, dry_run=True)
                print("\n🔍 DRY RUN completed - no changes made to database")
            
            return 0
            
        except Exception as e:
            print(f"❌ Cleanup process failed: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()
            return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Clean up duplicate share entries from Dirracuda database"
    )
    parser.add_argument(
        "--database", 
        type=str, 
        help="Path to database file (default: from config)"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Analyze only, don't make changes"
    )
    parser.add_argument(
        "--verbose", 
        action="store_true", 
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    try:
        # Get database path
        if args.database:
            db_path = args.database
        else:
            config = load_config()
            db_path = config.get_database_path()
        
        print(f"🗄️  Dirracuda Database Cleanup: Remove Duplicate Share Entries")
        print(f"Database: {db_path}")
        
        if not os.path.exists(db_path):
            print(f"❌ Database file not found: {db_path}")
            return 1
        
        # Create backup of entire database file
        if not args.dry_run:
            backup_path = f"{db_path}.cleanup_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            import shutil
            shutil.copy2(db_path, backup_path)
            print(f"📦 Full database backup created: {backup_path}")
        
        # Run cleanup
        cleanup = DatabaseCleanup(db_path, verbose=args.verbose)
        if not cleanup.connect():
            return 1
        
        try:
            return cleanup.run_cleanup(dry_run=args.dry_run)
        finally:
            cleanup.close()
    
    except Exception as e:
        print(f"❌ Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())