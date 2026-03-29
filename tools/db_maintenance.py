#!/usr/bin/env python3
"""
Dirracuda Database Maintenance Utilities
Database backup, cleanup, optimization, and maintenance operations
"""

import os
import sys
import shutil
import sqlite3
import argparse
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import glob

from db_manager import DatabaseManager, SMBSeekDataAccessLayer, resolve_tool_database_path


class SMBSeekDatabaseMaintenance:
    """
    Database maintenance utilities for Dirracuda SQLite database.
    
    Provides backup, cleanup, optimization, and health check operations.
    """
    
    def __init__(self, db_path: Optional[str] = None, config: Optional[Dict] = None):
        """
        Initialize database maintenance utility.
        
        Args:
            db_path: Path to SQLite database
            config: Configuration dictionary
        """
        self.db_path = resolve_tool_database_path(db_path)
        self.config = config or {}
        self.db_manager = DatabaseManager(self.db_path, config)
        self.dal = SMBSeekDataAccessLayer(self.db_manager)
        self.logger = logging.getLogger(__name__)
        
        # Get backup configuration
        db_config = self.config.get('database', {})
        self.backup_dir = db_config.get('backup_directory', 'db_backups')
        self.max_backup_files = db_config.get('max_backup_files', 30)
        
        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def create_backup(self, backup_name: Optional[str] = None) -> str:
        """
        Create a backup of the database.
        
        Args:
            backup_name: Custom backup filename (optional)
        
        Returns:
            Path to backup file
        """
        if not backup_name:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_name = f"smbseek_backup_{timestamp}.db"
        
        backup_path = os.path.join(self.backup_dir, backup_name)
        
        try:
            # Use SQLite backup API for consistent backup
            source_conn = sqlite3.connect(self.db_path)
            backup_conn = sqlite3.connect(backup_path)
            
            source_conn.backup(backup_conn)
            
            backup_conn.close()
            source_conn.close()
            
            # Create metadata file
            metadata = {
                'backup_timestamp': datetime.now().isoformat(),
                'source_database': os.path.abspath(self.db_path),
                'backup_size': os.path.getsize(backup_path),
                'backup_tool': 'db_maintenance.py'
            }
            
            metadata_path = backup_path + '.meta'
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            self.logger.info(f"Database backup created: {backup_path}")
            return backup_path
            
        except Exception as e:
            self.logger.error(f"Backup failed: {e}")
            # Clean up partial backup
            if os.path.exists(backup_path):
                os.remove(backup_path)
            raise
    
    def cleanup_old_backups(self) -> int:
        """
        Clean up old backup files based on retention policy.
        
        Returns:
            Number of files cleaned up
        """
        if self.max_backup_files <= 0:
            return 0
        
        # Find all backup files
        backup_pattern = os.path.join(self.backup_dir, "smbseek_backup_*.db")
        backup_files = glob.glob(backup_pattern)
        
        if len(backup_files) <= self.max_backup_files:
            return 0
        
        # Sort by creation time (oldest first)
        backup_files.sort(key=lambda x: os.path.getctime(x))
        
        # Remove oldest files
        files_to_remove = backup_files[:-self.max_backup_files]
        removed_count = 0
        
        for backup_file in files_to_remove:
            try:
                os.remove(backup_file)
                # Also remove metadata file if it exists
                meta_file = backup_file + '.meta'
                if os.path.exists(meta_file):
                    os.remove(meta_file)
                removed_count += 1
                self.logger.info(f"Removed old backup: {backup_file}")
            except Exception as e:
                self.logger.error(f"Failed to remove backup {backup_file}: {e}")
        
        return removed_count
    
    def vacuum_database(self) -> bool:
        """
        Run VACUUM command to reclaim space and defragment database.
        
        Returns:
            True if successful
        """
        try:
            conn = self.db_manager.get_connection()
            
            # Get database size before vacuum
            size_before = os.path.getsize(self.db_path)
            
            # Run vacuum
            conn.execute("VACUUM")
            conn.close()
            
            # Get database size after vacuum
            size_after = os.path.getsize(self.db_path)
            space_saved = size_before - size_after
            
            self.logger.info(f"Database vacuumed. Space reclaimed: {space_saved} bytes")
            return True
            
        except Exception as e:
            self.logger.error(f"Vacuum failed: {e}")
            return False
    
    def analyze_database(self) -> bool:
        """
        Run ANALYZE command to update database statistics for query optimization.
        
        Returns:
            True if successful
        """
        try:
            conn = self.db_manager.get_connection()
            conn.execute("ANALYZE")
            conn.close()
            
            self.logger.info("Database statistics updated")
            return True
            
        except Exception as e:
            self.logger.error(f"Analyze failed: {e}")
            return False
    
    def check_integrity(self) -> bool:
        """
        Run integrity check on database.
        
        Returns:
            True if database is healthy
        """
        try:
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()
            
            # Run integrity check
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            
            cursor.close()
            
            if result and result[0] == "ok":
                self.logger.info("Database integrity check: OK")
                return True
            else:
                self.logger.error(f"Database integrity check failed: {result}")
                return False
                
        except Exception as e:
            self.logger.error(f"Integrity check failed: {e}")
            return False
    
    def get_database_info(self) -> Dict:
        """
        Get comprehensive database information and statistics.
        
        Returns:
            Dictionary with database information
        """
        try:
            conn = self.db_manager.get_connection()
            cursor = conn.cursor()
            
            info = {}
            
            # Basic file information
            info['file_path'] = os.path.abspath(self.db_path)
            info['file_size'] = os.path.getsize(self.db_path)
            info['last_modified'] = datetime.fromtimestamp(
                os.path.getmtime(self.db_path)
            ).isoformat()
            
            # Database version and settings
            cursor.execute("PRAGMA user_version")
            info['user_version'] = cursor.fetchone()[0]
            
            cursor.execute("PRAGMA journal_mode")
            info['journal_mode'] = cursor.fetchone()[0]
            
            cursor.execute("PRAGMA foreign_keys")
            info['foreign_keys'] = bool(cursor.fetchone()[0])
            
            # Table information
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            tables = [row[0] for row in cursor.fetchall()]
            info['tables'] = {}
            
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                info['tables'][table] = count
            
            # Index information
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='index' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            indices = [row[0] for row in cursor.fetchall()]
            info['indices'] = indices
            
            # View information
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='view'
                ORDER BY name
            """)
            views = [row[0] for row in cursor.fetchall()]
            info['views'] = views
            
            cursor.close()
            
            return info
            
        except Exception as e:
            self.logger.error(f"Failed to get database info: {e}")
            return {}
    
    def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """
        Clean up old data based on age.
        
        Args:
            days: Age threshold in days
        
        Returns:
            Dictionary with cleanup statistics
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        
        stats = {}
        
        try:
            # Clean up old scan sessions and related data
            old_sessions = self.db_manager.execute_query("""
                SELECT id FROM scan_sessions 
                WHERE timestamp < ? AND status = 'completed'
            """, (cutoff_str,))
            
            session_ids = [row['id'] for row in old_sessions]
            
            if session_ids:
                # Delete related data first (due to foreign key constraints)
                for table in ['share_access', 'file_manifests', 'vulnerabilities']:
                    placeholders = ','.join(['?' for _ in session_ids])
                    query = f"DELETE FROM {table} WHERE session_id IN ({placeholders})"
                    result = self.db_manager.execute_update(table, {}, f"session_id IN ({placeholders})", tuple(session_ids))
                    stats[f"{table}_deleted"] = result
                
                # Delete scan sessions
                placeholders = ','.join(['?' for _ in session_ids])
                result = self.db_manager.execute_update('scan_sessions', {}, f"id IN ({placeholders})", tuple(session_ids))
                stats['sessions_deleted'] = result
            
            # Clean up old failure logs
            result = self.db_manager.execute_update(
                'failure_logs', {}, 
                "failure_timestamp < ?", (cutoff_str,)
            )
            stats['failure_logs_deleted'] = result
            
            # Clean up servers with no recent activity
            result = self.db_manager.execute_update(
                'smb_servers', {'status': 'inactive'}, 
                "last_seen < ? AND status = 'active'", (cutoff_str,)
            )
            stats['servers_deactivated'] = result
            
            self.logger.info(f"Cleaned up data older than {days} days")
            return stats
            
        except Exception as e:
            self.logger.error(f"Data cleanup failed: {e}")
            return {'error': str(e)}
    
    def export_to_csv(self, output_dir: str = "exports") -> List[str]:
        """
        Export database tables to CSV files.
        
        Args:
            output_dir: Directory for CSV exports
        
        Returns:
            List of created CSV files
        """
        os.makedirs(output_dir, exist_ok=True)
        
        exported_files = []
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Tables to export
        tables = ['smb_servers', 'scan_sessions', 'share_access', 
                 'file_manifests', 'vulnerabilities', 'failure_logs']
        
        try:
            conn = self.db_manager.get_connection()
            
            for table in tables:
                csv_file = os.path.join(output_dir, f"{table}_{timestamp}.csv")
                
                cursor = conn.cursor()
                cursor.execute(f"SELECT * FROM {table}")
                
                # Get column names
                columns = [description[0] for description in cursor.description]
                
                # Write CSV file
                import csv
                with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(columns)
                    writer.writerows(cursor.fetchall())
                
                cursor.close()
                exported_files.append(csv_file)
                self.logger.info(f"Exported {table} to {csv_file}")
            
            return exported_files
            
        except Exception as e:
            self.logger.error(f"CSV export failed: {e}")
            return []
    
    def run_maintenance(self, full: bool = False) -> Dict[str, bool]:
        """
        Run routine database maintenance.
        
        Args:
            full: Whether to run full maintenance (including vacuum)
        
        Returns:
            Dictionary with operation results
        """
        results = {}
        
        # Always run these operations
        results['integrity_check'] = self.check_integrity()
        results['analyze'] = self.analyze_database()
        results['backup'] = bool(self.create_backup())
        results['cleanup_backups'] = self.cleanup_old_backups() >= 0
        
        # Full maintenance operations
        if full:
            results['vacuum'] = self.vacuum_database()
            results['cleanup_data'] = bool(self.cleanup_old_data(days=90))
        
        return results


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Dirracuda database maintenance utilities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 db_maintenance.py --backup
  python3 db_maintenance.py --vacuum
  python3 db_maintenance.py --maintenance
  python3 db_maintenance.py --info
  python3 db_maintenance.py --export
        """
    )
    
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: auto-detect dirracuda.db then smbseek.db)",
    )
    parser.add_argument("--backup", action="store_true",
                       help="Create database backup")
    parser.add_argument("--vacuum", action="store_true",
                       help="Vacuum database to reclaim space")
    parser.add_argument("--analyze", action="store_true",
                       help="Update database statistics")
    parser.add_argument("--check", action="store_true",
                       help="Run integrity check")
    parser.add_argument("--maintenance", action="store_true",
                       help="Run routine maintenance")
    parser.add_argument("--full-maintenance", action="store_true",
                       help="Run full maintenance (includes vacuum)")
    parser.add_argument("--cleanup", type=int, metavar="DAYS",
                       help="Clean up data older than N days")
    parser.add_argument("--info", action="store_true",
                       help="Show database information")
    parser.add_argument("--export", action="store_true",
                       help="Export tables to CSV files")
    parser.add_argument("--config", 
                       help="Configuration file path")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Load configuration
    config = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, 'r') as f:
            config = json.load(f)
    else:
        # Default to conf/config.json, handling path from tools/ directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        default_config = os.path.join(os.path.dirname(script_dir), "conf", "config.json")
        if os.path.exists(default_config):
            with open(default_config, 'r') as f:
                config = json.load(f)
    
    # Initialize maintenance utility
    maintenance = SMBSeekDatabaseMaintenance(db_path=args.db_path, config=config)
    
    try:
        if args.backup:
            backup_path = maintenance.create_backup()
            print(f"Backup created: {backup_path}")
        
        elif args.vacuum:
            if maintenance.vacuum_database():
                print("Database vacuumed successfully")
            else:
                print("Vacuum failed")
                return 1
        
        elif args.analyze:
            if maintenance.analyze_database():
                print("Database statistics updated")
            else:
                print("Analyze failed")
                return 1
        
        elif args.check:
            if maintenance.check_integrity():
                print("Database integrity: OK")
            else:
                print("Database integrity check failed")
                return 1
        
        elif args.cleanup:
            stats = maintenance.cleanup_old_data(days=args.cleanup)
            print(f"Cleanup completed: {stats}")
        
        elif args.info:
            info = maintenance.get_database_info()
            print(json.dumps(info, indent=2))
        
        elif args.export:
            files = maintenance.export_to_csv()
            print(f"Exported {len(files)} CSV files")
            for file in files:
                print(f"  {file}")
        
        elif args.maintenance or args.full_maintenance:
            results = maintenance.run_maintenance(full=args.full_maintenance)
            print("Maintenance results:")
            for operation, success in results.items():
                status = "SUCCESS" if success else "FAILED"
                print(f"  {operation}: {status}")
        
        else:
            # Default: show database info
            info = maintenance.get_database_info()
            print("Database Information:")
            print(f"  Path: {info.get('file_path')}")
            print(f"  Size: {info.get('file_size', 0):,} bytes")
            print(f"  Tables: {len(info.get('tables', {}))}")
            print(f"  Total records: {sum(info.get('tables', {}).values())}")
        
        return 0
        
    except Exception as e:
        logging.error(f"Maintenance operation failed: {e}")
        return 1
    finally:
        maintenance.db_manager.close()


if __name__ == "__main__":
    sys.exit(main())
