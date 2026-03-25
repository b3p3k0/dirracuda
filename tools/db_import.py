#!/usr/bin/env python3
"""
Dirracuda Database Import Utilities
Import existing CSV and JSON data files into SQLite database
"""

import csv
import json
import os
import sys
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging
import glob

from db_manager import DatabaseManager, SMBSeekDataAccessLayer


class SMBSeekDataImporter:
    """
    Import utility for migrating existing Dirracuda data files to SQLite database.
    
    Supports importing:
    - ip_record.csv files (scan results)
    - failed_record.csv files (failure logs)
    - share_access_*.json files (share accessibility data)
    - file_manifest_*.json files (file discovery data)
    - failure_analysis_*.json files (failure analysis results)
    - vulnerability_report_*.json files (vulnerability data)
    """
    
    def __init__(self, db_path: str = "smbseek.db", config: Optional[Dict] = None):
        """
        Initialize data importer.
        
        Args:
            db_path: Path to SQLite database
            config: Configuration dictionary
        """
        self.db_manager = DatabaseManager(db_path, config)
        self.dal = SMBSeekDataAccessLayer(self.db_manager)
        self.logger = logging.getLogger(__name__)
        
        # Import statistics
        self.stats = {
            'servers_imported': 0,
            'sessions_created': 0,
            'shares_imported': 0,
            'files_imported': 0,
            'vulnerabilities_imported': 0,
            'failures_imported': 0,
            'errors': 0
        }
    
    def import_ip_record_csv(self, csv_file: str) -> bool:
        """
        Import server data from ip_record.csv format.
        
        Expected CSV columns: ip_address, country, auth_method, shares, timestamp
        
        Args:
            csv_file: Path to CSV file
        
        Returns:
            True if import successful
        """
        if not os.path.exists(csv_file):
            self.logger.error(f"CSV file not found: {csv_file}")
            return False
        
        try:
            # Create scan session for this import
            session_id = self.dal.create_scan_session(
                tool_name="csv_import",
                config_snapshot={"source_file": csv_file, "import_timestamp": datetime.now().isoformat()}
            )
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    try:
                        ip_address = row.get('ip_address', '').strip()
                        if not ip_address:
                            continue
                        
                        country = row.get('country', '').strip() or None
                        auth_method = row.get('auth_method', '').strip() or None
                        shares_str = row.get('shares', '').strip()
                        timestamp_str = row.get('timestamp', '').strip()
                        
                        # Get or create server (country_code=None for legacy imports)
                        server_id = self.dal.get_or_create_server(
                            ip_address=ip_address,
                            country=country,
                            auth_method=auth_method,
                            country_code=None
                        )
                        
                        # Parse shares if available
                        if shares_str:
                            share_names = [s.strip() for s in shares_str.split(',')]
                            for share_name in share_names:
                                if share_name and share_name != '(and more)':
                                    self.dal.add_share_access(
                                        server_id=server_id,
                                        session_id=session_id,
                                        share_name=share_name,
                                        accessible=True,  # Assume accessible if listed in CSV
                                        test_timestamp=timestamp_str or datetime.now().isoformat()
                                    )
                                    self.stats['shares_imported'] += 1
                        
                        self.stats['servers_imported'] += 1
                        
                    except Exception as e:
                        self.logger.error(f"Error importing row from {csv_file}: {e}")
                        self.stats['errors'] += 1
            
            # Update session status
            self.dal.update_scan_session(session_id, status='completed')
            self.stats['sessions_created'] += 1
            
            self.logger.info(f"Successfully imported {csv_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to import {csv_file}: {e}")
            self.stats['errors'] += 1
            return False
    
    def import_failed_record_csv(self, csv_file: str) -> bool:
        """
        Import failure data from failed_record.csv format.
        
        Expected CSV columns: ip_address, country, failure_reason, timestamp
        
        Args:
            csv_file: Path to CSV file
        
        Returns:
            True if import successful
        """
        if not os.path.exists(csv_file):
            self.logger.error(f"CSV file not found: {csv_file}")
            return False
        
        try:
            # Create scan session for this import
            session_id = self.dal.create_scan_session(
                tool_name="failed_csv_import",
                config_snapshot={"source_file": csv_file, "import_timestamp": datetime.now().isoformat()}
            )
            
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    try:
                        ip_address = row.get('ip_address', '').strip()
                        if not ip_address:
                            continue
                        
                        failure_reason = row.get('failure_reason', '').strip() or 'Unknown'
                        timestamp_str = row.get('timestamp', '').strip()
                        
                        self.dal.add_failure_log(
                            ip_address=ip_address,
                            failure_type='connection_failed',
                            failure_reason=failure_reason,
                            session_id=session_id
                        )
                        
                        self.stats['failures_imported'] += 1
                        
                    except Exception as e:
                        self.logger.error(f"Error importing failure row from {csv_file}: {e}")
                        self.stats['errors'] += 1
            
            # Update session status
            self.dal.update_scan_session(session_id, status='completed')
            self.stats['sessions_created'] += 1
            
            self.logger.info(f"Successfully imported {csv_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to import {csv_file}: {e}")
            self.stats['errors'] += 1
            return False
    
    def import_share_access_json(self, json_file: str) -> bool:
        """
        Import share access data from share_access_*.json format.
        
        Args:
            json_file: Path to JSON file
        
        Returns:
            True if import successful
        """
        if not os.path.exists(json_file):
            self.logger.error(f"JSON file not found: {json_file}")
            return False
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            metadata = data.get('metadata', {})
            results = data.get('results', [])
            
            # Create scan session
            session_id = self.dal.create_scan_session(
                tool_name=metadata.get('tool', 'smb_peep'),
                config_snapshot={
                    "source_file": json_file,
                    "original_metadata": metadata,
                    "import_timestamp": datetime.now().isoformat()
                }
            )
            
            for result in results:
                try:
                    ip_address = result.get('ip_address', '').strip()
                    if not ip_address:
                        continue
                    
                    country = result.get('country', '').strip() or None
                    auth_method = result.get('auth_method', '').strip() or None

                    # Get or create server (country_code=None for legacy imports)
                    server_id = self.dal.get_or_create_server(
                        ip_address=ip_address,
                        country=country,
                        auth_method=auth_method,
                        country_code=None
                    )
                    
                    # Import accessible shares
                    accessible_shares = result.get('accessible_shares', [])
                    for share_name in accessible_shares:
                        self.dal.add_share_access(
                            server_id=server_id,
                            session_id=session_id,
                            share_name=share_name,
                            accessible=True,
                            test_timestamp=result.get('timestamp', datetime.now().isoformat())
                        )
                        self.stats['shares_imported'] += 1
                    
                    # Import share details
                    share_details = result.get('share_details', [])
                    for detail in share_details:
                        share_name = detail.get('share_name', '')
                        if share_name:
                            self.dal.add_share_access(
                                server_id=server_id,
                                session_id=session_id,
                                share_name=share_name,
                                accessible=detail.get('accessible', False),
                                permissions=json.dumps(detail.get('permissions', [])),
                                share_type=detail.get('share_type'),
                                share_comment=detail.get('comment'),
                                access_details=json.dumps(detail),
                                test_timestamp=result.get('timestamp', datetime.now().isoformat())
                            )
                    
                    self.stats['servers_imported'] += 1
                    
                except Exception as e:
                    self.logger.error(f"Error importing result from {json_file}: {e}")
                    self.stats['errors'] += 1
            
            # Update session status
            total_targets = len(results)
            successful_targets = len([r for r in results if r.get('accessible_shares')])
            
            self.dal.update_scan_session(
                session_id,
                status='completed',
                total_targets=total_targets,
                successful_targets=successful_targets
            )
            
            self.stats['sessions_created'] += 1
            self.logger.info(f"Successfully imported {json_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to import {json_file}: {e}")
            self.stats['errors'] += 1
            return False
    
    def import_file_manifest_json(self, json_file: str) -> bool:
        """
        Import file manifest data from file_manifest_*.json format.
        
        Args:
            json_file: Path to JSON file
        
        Returns:
            True if import successful
        """
        if not os.path.exists(json_file):
            self.logger.error(f"JSON file not found: {json_file}")
            return False
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            metadata = data.get('metadata', {})
            servers = data.get('servers', [])
            
            # Create scan session
            session_id = self.dal.create_scan_session(
                tool_name=metadata.get('tool', 'smb_snag'),
                config_snapshot={
                    "source_file": json_file,
                    "original_metadata": metadata,
                    "import_timestamp": datetime.now().isoformat()
                }
            )
            
            for server in servers:
                try:
                    ip_address = server.get('ip_address', '').strip()
                    if not ip_address:
                        continue
                    
                    country = server.get('country', '').strip() or None
                    auth_method = server.get('auth_method', '').strip() or None

                    # Get or create server (country_code=None for legacy imports)
                    server_id = self.dal.get_or_create_server(
                        ip_address=ip_address,
                        country=country,
                        auth_method=auth_method,
                        country_code=None
                    )
                    
                    # Import file discoveries
                    shares = server.get('shares', [])
                    for share in shares:
                        share_name = share.get('share_name', '')
                        files = share.get('files', [])
                        
                        for file_info in files:
                            file_path = file_info.get('file_path', '')
                            if not file_path:
                                continue
                            
                            # Check for ransomware indicators
                            is_ransomware = file_info.get('is_ransomware_indicator', False)
                            
                            self.dal.add_file_manifest(
                                server_id=server_id,
                                session_id=session_id,
                                share_name=share_name,
                                file_path=file_path,
                                file_size=file_info.get('file_size', 0),
                                file_type=file_info.get('file_type'),
                                last_modified=file_info.get('last_modified'),
                                is_ransomware_indicator=is_ransomware,
                                metadata=json.dumps(file_info)
                            )
                            
                            self.stats['files_imported'] += 1
                    
                    self.stats['servers_imported'] += 1
                    
                except Exception as e:
                    self.logger.error(f"Error importing server from {json_file}: {e}")
                    self.stats['errors'] += 1
            
            # Update session status
            self.dal.update_scan_session(
                session_id,
                status='completed',
                total_targets=metadata.get('total_servers', 0)
            )
            
            self.stats['sessions_created'] += 1
            self.logger.info(f"Successfully imported {json_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to import {json_file}: {e}")
            self.stats['errors'] += 1
            return False
    
    def import_vulnerability_report_json(self, json_file: str) -> bool:
        """
        Import vulnerability data from vulnerability_report_*.json format.
        
        Args:
            json_file: Path to JSON file
        
        Returns:
            True if import successful
        """
        if not os.path.exists(json_file):
            self.logger.error(f"JSON file not found: {json_file}")
            return False
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            metadata = data.get('metadata', {})
            vulnerabilities = data.get('vulnerabilities', [])
            
            # Create scan session
            session_id = self.dal.create_scan_session(
                tool_name=metadata.get('tool', 'smb_vuln'),
                config_snapshot={
                    "source_file": json_file,
                    "original_metadata": metadata,
                    "import_timestamp": datetime.now().isoformat()
                }
            )
            
            for vuln in vulnerabilities:
                try:
                    ip_address = vuln.get('ip_address', '').strip()
                    if not ip_address:
                        continue
                    
                    # Get or create server (country_code=None for legacy imports)
                    server_id = self.dal.get_or_create_server(ip_address=ip_address, country_code=None)
                    
                    # Import vulnerability
                    self.dal.add_vulnerability(
                        server_id=server_id,
                        session_id=session_id,
                        vuln_type=vuln.get('type', 'unknown'),
                        severity=vuln.get('severity', 'medium'),
                        title=vuln.get('title', 'Vulnerability'),
                        description=vuln.get('description'),
                        evidence=json.dumps(vuln.get('evidence', {})),
                        remediation=vuln.get('remediation'),
                        cvss_score=vuln.get('cvss_score'),
                        discovery_timestamp=vuln.get('timestamp', datetime.now().isoformat())
                    )
                    
                    self.stats['vulnerabilities_imported'] += 1
                    
                except Exception as e:
                    self.logger.error(f"Error importing vulnerability from {json_file}: {e}")
                    self.stats['errors'] += 1
            
            # Update session status
            self.dal.update_scan_session(
                session_id,
                status='completed',
                total_targets=len(vulnerabilities)
            )
            
            self.stats['sessions_created'] += 1
            self.logger.info(f"Successfully imported {json_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to import {json_file}: {e}")
            self.stats['errors'] += 1
            return False
    
    def import_directory(self, directory: str = ".") -> Dict[str, int]:
        """
        Import all supported data files from a directory.
        
        Args:
            directory: Directory to scan for data files
        
        Returns:
            Import statistics dictionary
        """
        if not os.path.isdir(directory):
            self.logger.error(f"Directory not found: {directory}")
            return self.stats
        
        # Find and import CSV files
        csv_patterns = [
            "ip_record*.csv",
            "failed_record*.csv"
        ]
        
        for pattern in csv_patterns:
            files = glob.glob(os.path.join(directory, pattern))
            for file_path in files:
                if "failed_record" in os.path.basename(file_path):
                    self.import_failed_record_csv(file_path)
                else:
                    self.import_ip_record_csv(file_path)
        
        # Find and import JSON files
        json_patterns = [
            "share_access_*.json",
            "file_manifest_*.json",
            "vulnerability_report_*.json"
        ]
        
        for pattern in json_patterns:
            files = glob.glob(os.path.join(directory, pattern))
            for file_path in files:
                if "share_access" in os.path.basename(file_path):
                    self.import_share_access_json(file_path)
                elif "file_manifest" in os.path.basename(file_path):
                    self.import_file_manifest_json(file_path)
                elif "vulnerability_report" in os.path.basename(file_path):
                    self.import_vulnerability_report_json(file_path)
        
        return self.stats
    
    def print_import_summary(self):
        """Print summary of import statistics."""
        print(f"\n{'='*50}")
        print("IMPORT SUMMARY")
        print(f"{'='*50}")
        print(f"Servers imported:        {self.stats['servers_imported']}")
        print(f"Sessions created:        {self.stats['sessions_created']}")
        print(f"Shares imported:         {self.stats['shares_imported']}")
        print(f"Files imported:          {self.stats['files_imported']}")
        print(f"Vulnerabilities imported: {self.stats['vulnerabilities_imported']}")
        print(f"Failures imported:       {self.stats['failures_imported']}")
        print(f"Errors encountered:      {self.stats['errors']}")
        print(f"{'='*50}")


def main():
    """Main function for command-line usage."""
    parser = argparse.ArgumentParser(
        description="Import Dirracuda data files into SQLite database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 db_import.py --directory .
  python3 db_import.py --csv ip_record.csv
  python3 db_import.py --json share_access_20250819_162025.json
  python3 db_import.py --all
        """
    )
    
    parser.add_argument("--directory", "-d", 
                       help="Import all supported files from directory (default: current)")
    parser.add_argument("--csv", 
                       help="Import specific CSV file")
    parser.add_argument("--json", 
                       help="Import specific JSON file")
    parser.add_argument("--all", action="store_true",
                       help="Import all supported files from current directory")
    parser.add_argument("--db-path", default="smbseek.db",
                       help="SQLite database path (default: smbseek.db)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # Initialize importer
    importer = SMBSeekDataImporter(db_path=args.db_path)
    
    try:
        if args.csv:
            if "failed" in args.csv.lower():
                importer.import_failed_record_csv(args.csv)
            else:
                importer.import_ip_record_csv(args.csv)
        elif args.json:
            if "share_access" in args.json:
                importer.import_share_access_json(args.json)
            elif "file_manifest" in args.json:
                importer.import_file_manifest_json(args.json)
            elif "vulnerability" in args.json:
                importer.import_vulnerability_report_json(args.json)
            else:
                print(f"Unknown JSON file type: {args.json}")
                return 1
        elif args.directory or args.all:
            directory = args.directory if args.directory else "."
            importer.import_directory(directory)
        else:
            # Default: import from current directory
            importer.import_directory(".")
        
        # Print summary
        importer.print_import_summary()
        
        return 0
        
    except Exception as e:
        logging.error(f"Import failed: {e}")
        return 1
    finally:
        importer.db_manager.close()


if __name__ == "__main__":
    sys.exit(main())