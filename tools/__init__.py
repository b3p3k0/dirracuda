"""
Dirracuda Tools Module

This module contains all Dirracuda security analysis tools and database utilities.

SMB Tools:
- smb_scan: Primary scanner for discovering SMB servers with weak authentication
- smb_peep: Share access verification tool for testing read accessibility  
- smb_snag: File collection tool for downloading samples from accessible shares
- smb_vuln: Vulnerability assessment tool for testing specific CVEs
- failure_analyzer: Deep analysis tool for understanding authentication failures

Database Tools:
- db_manager: Core database connection and transaction management
- db_query: User-friendly database querying and reporting
- db_import: Import existing CSV/JSON data files into database
- db_maintenance: Database backup, optimization, and cleanup utilities

Usage:
    python3 tools/smb_scan.py -c US
    python3 tools/db_query.py --summary
    python3 tools/db_maintenance.py --backup
"""

__version__ = "1.0.0"
__author__ = "Dirracuda Development Team"