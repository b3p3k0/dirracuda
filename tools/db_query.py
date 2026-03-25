#!/usr/bin/env python3
"""
Dirracuda Database Query Utility
Basic database queries and reporting for SQLite database
"""

import sqlite3
import json
import argparse
import sys
from datetime import datetime
from db_manager import DatabaseManager, SMBSeekDataAccessLayer, resolve_tool_database_path


def query_server_summary(db_path: str):
    """Display server summary statistics."""
    db_manager = DatabaseManager(db_path)
    dal = SMBSeekDataAccessLayer(db_manager)
    
    print("=" * 60)
    print("SERVER SUMMARY")
    print("=" * 60)
    
    servers = dal.get_server_summary(limit=10)
    if not servers:
        print("No servers found in database.")
        return
    
    print(f"{'IP Address':<15} {'Country':<15} {'Auth Method':<15} {'Shares':<6} {'Files':<6}")
    print("-" * 60)
    
    for server in servers:
        print(f"{server['ip_address']:<15} "
              f"{(server['country'] or 'Unknown')[:14]:<15} "
              f"{(server['auth_method'] or 'Unknown')[:14]:<15} "
              f"{server['accessible_shares_count'] or 0:<6} "
              f"{server['files_discovered'] or 0:<6}")
    
    # Overall statistics
    total_servers = db_manager.execute_query("SELECT COUNT(*) as count FROM smb_servers")[0]['count']
    total_shares = db_manager.execute_query("SELECT COUNT(*) as count FROM share_access WHERE accessible = TRUE")[0]['count']
    total_files = db_manager.execute_query("SELECT COUNT(*) as count FROM file_manifests")[0]['count']
    
    print("-" * 60)
    print(f"Total Servers: {total_servers}")
    print(f"Total Accessible Shares: {total_shares}")
    print(f"Total Files Discovered: {total_files}")


def query_vulnerability_summary(db_path: str):
    """Display vulnerability summary statistics."""
    db_manager = DatabaseManager(db_path)
    dal = SMBSeekDataAccessLayer(db_manager)
    
    print("\n" + "=" * 60)
    print("VULNERABILITY SUMMARY")
    print("=" * 60)
    
    vulnerabilities = dal.get_vulnerability_summary()
    if not vulnerabilities:
        print("No vulnerabilities found in database.")
        return
    
    print(f"{'Vulnerability Type':<25} {'Severity':<10} {'Count':<6} {'Servers':<8}")
    print("-" * 60)
    
    for vuln in vulnerabilities:
        print(f"{vuln['vuln_type'][:24]:<25} "
              f"{vuln['severity']:<10} "
              f"{vuln['count']:<6} "
              f"{vuln['affected_servers']:<8}")


def query_scan_statistics(db_path: str, days: int = 30):
    """Display scan statistics."""
    db_manager = DatabaseManager(db_path)
    dal = SMBSeekDataAccessLayer(db_manager)
    
    print("\n" + "=" * 60)
    print(f"SCAN STATISTICS (Last {days} days)")
    print("=" * 60)
    
    stats = dal.get_scan_statistics(days)
    if not stats:
        print("No scan statistics found.")
        return
    
    print(f"{'Tool':<15} {'Date':<12} {'Sessions':<8} {'Success Rate':<12}")
    print("-" * 60)
    
    for stat in stats:
        success_rate = stat['success_rate'] or 0
        print(f"{stat['tool_name'][:14]:<15} "
              f"{stat['scan_date']:<12} "
              f"{stat['sessions']:<8} "
              f"{success_rate:.1f}%")


def query_country_distribution(db_path: str):
    """Display country distribution of servers."""
    db_manager = DatabaseManager(db_path)
    
    print("\n" + "=" * 40)
    print("COUNTRY DISTRIBUTION")
    print("=" * 40)
    
    results = db_manager.execute_query("""
        SELECT 
            country,
            COUNT(*) as server_count,
            COUNT(DISTINCT sa.server_id) as servers_with_shares
        FROM smb_servers s
        LEFT JOIN share_access sa ON s.id = sa.server_id AND sa.accessible = TRUE
        WHERE s.status = 'active'
        GROUP BY country
        ORDER BY server_count DESC
        LIMIT 10
    """)
    
    print(f"{'Country':<20} {'Servers':<8} {'With Shares':<12}")
    print("-" * 40)
    
    for row in results:
        country = row['country'] or 'Unknown'
        print(f"{country[:19]:<20} "
              f"{row['server_count']:<8} "
              f"{row['servers_with_shares'] or 0:<12}")


def query_auth_methods(db_path: str):
    """Display authentication method distribution."""
    db_manager = DatabaseManager(db_path)
    
    print("\n" + "=" * 50)
    print("AUTHENTICATION METHOD DISTRIBUTION")
    print("=" * 50)
    
    results = db_manager.execute_query("""
        SELECT 
            auth_method,
            COUNT(*) as server_count,
            AVG(scan_count) as avg_scans
        FROM smb_servers
        WHERE status = 'active'
        GROUP BY auth_method
        ORDER BY server_count DESC
    """)
    
    print(f"{'Auth Method':<25} {'Servers':<8} {'Avg Scans':<10}")
    print("-" * 50)
    
    for row in results:
        auth_method = row['auth_method'] or 'Unknown'
        avg_scans = row['avg_scans'] or 0
        print(f"{auth_method[:24]:<25} "
              f"{row['server_count']:<8} "
              f"{avg_scans:.1f}")


def query_top_shares(db_path: str):
    """Display most common share names."""
    db_manager = DatabaseManager(db_path)
    
    print("\n" + "=" * 40)
    print("MOST COMMON SHARE NAMES")
    print("=" * 40)
    
    results = db_manager.execute_query("""
        SELECT 
            share_name,
            COUNT(*) as occurrence_count,
            COUNT(DISTINCT server_id) as server_count
        FROM share_access
        WHERE accessible = TRUE
        GROUP BY share_name
        ORDER BY occurrence_count DESC
        LIMIT 15
    """)
    
    print(f"{'Share Name':<20} {'Occurrences':<12} {'Servers':<8}")
    print("-" * 40)
    
    for row in results:
        print(f"{row['share_name'][:19]:<20} "
              f"{row['occurrence_count']:<12} "
              f"{row['server_count']:<8}")


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Query Dirracuda SQLite database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 db_query.py --summary
  python3 db_query.py --vulnerabilities
  python3 db_query.py --statistics
  python3 db_query.py --all
        """
    )
    
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite database path (default: auto-detect dirracuda.db then smbseek.db)",
    )
    parser.add_argument("--summary", action="store_true",
                       help="Show server summary")
    parser.add_argument("--vulnerabilities", action="store_true",
                       help="Show vulnerability summary")
    parser.add_argument("--statistics", action="store_true",
                       help="Show scan statistics")
    parser.add_argument("--countries", action="store_true",
                       help="Show country distribution")
    parser.add_argument("--auth", action="store_true",
                       help="Show authentication method distribution")
    parser.add_argument("--shares", action="store_true",
                       help="Show most common shares")
    parser.add_argument("--all", action="store_true",
                       help="Show all reports")
    parser.add_argument("--days", type=int, default=30,
                       help="Days to include in statistics (default: 30)")
    
    args = parser.parse_args()
    
    if not any([args.summary, args.vulnerabilities, args.statistics, 
                args.countries, args.auth, args.shares, args.all]):
        # Default to summary
        args.summary = True
    
    effective_db_path = resolve_tool_database_path(args.db_path)

    try:
        if args.all:
            query_server_summary(effective_db_path)
            query_vulnerability_summary(effective_db_path)
            query_scan_statistics(effective_db_path, args.days)
            query_country_distribution(effective_db_path)
            query_auth_methods(effective_db_path)
            query_top_shares(effective_db_path)
        else:
            if args.summary:
                query_server_summary(effective_db_path)
            if args.vulnerabilities:
                query_vulnerability_summary(effective_db_path)
            if args.statistics:
                query_scan_statistics(effective_db_path, args.days)
            if args.countries:
                query_country_distribution(effective_db_path)
            if args.auth:
                query_auth_methods(effective_db_path)
            if args.shares:
                query_top_shares(effective_db_path)
        
        return 0
        
    except Exception as e:
        print(f"Query failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
