"""
SMBSeek Shared Output and Reporting

Consistent output formatting, executive reporting, and display utilities
for the unified SMBSeek CLI.
"""

import os
import json
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
import sys

# Add tools directory to path for database imports
tools_path = os.path.join(os.path.dirname(__file__), '..', 'tools')
sys.path.insert(0, tools_path)


class SMBSeekOutput:
    """
    Unified output management for SMBSeek CLI.
    
    Provides consistent formatting, color management, and executive reporting
    across all command modules.
    """
    
    def __init__(self, config, quiet: bool = False, verbose: bool = False, no_colors: bool = False):
        """
        Initialize output manager.

        Args:
            config: SMBSeekConfig instance
            quiet: Suppress output to screen
            verbose: Enable verbose output
            no_colors: Disable colored output
        """
        self.config = config
        self.quiet = quiet
        self.verbose = verbose

        # Protect console output across threads
        self._print_lock = threading.Lock()

        # Color management
        colors_enabled = config.get("output", "colors_enabled", True) and not no_colors
        if colors_enabled:
            self.GREEN = '\033[92m'
            self.RED = '\033[91m'
            self.YELLOW = '\033[93m'
            self.CYAN = '\033[96m'
            self.BLUE = '\033[94m'
            self.MAGENTA = '\033[95m'
            self.RESET = '\033[0m'
            self.BOLD = '\033[1m'
        else:
            self.GREEN = self.RED = self.YELLOW = self.CYAN = ''
            self.BLUE = self.MAGENTA = self.RESET = self.BOLD = ''
    
    def print_if_not_quiet(self, message: str):
        """Print message unless in quiet mode."""
        if not self.quiet:
            with self._print_lock:
                print(message)

    def print_if_verbose(self, message: str):
        """Print message only in verbose mode (and not quiet)."""
        if self.verbose and not self.quiet:
            with self._print_lock:
                print(message)
    
    def success(self, message: str):
        """Print success message with green color."""
        self.print_if_not_quiet(f"{self.GREEN}✓ {message}{self.RESET}")
    
    def error(self, message: str):
        """Print error message with red color."""
        self.print_if_not_quiet(f"{self.RED}✗ {message}{self.RESET}")
    
    def warning(self, message: str):
        """Print warning message with yellow color."""
        self.print_if_not_quiet(f"{self.YELLOW}⚠ {message}{self.RESET}")
    
    def info(self, message: str):
        """Print info message with blue color for cross-workflow visual parity."""
        self.print_if_not_quiet(f"{self.BLUE}ℹ {message}{self.RESET}")
    
    def header(self, message: str):
        """Print header message with bold formatting."""
        self.print_if_not_quiet(f"\n{self.BOLD}{message}{self.RESET}")
        self.print_if_not_quiet("=" * len(message))
    
    def subheader(self, message: str):
        """Print subheader message."""
        self.print_if_not_quiet(f"\n{self.CYAN}{message}{self.RESET}")
        self.print_if_not_quiet("-" * len(message))
    
    def workflow_step(self, step_name: str, step_number: int, total_steps: int):
        """Print workflow step indicator."""
        self.print_if_not_quiet(f"\n{self.BLUE}[{step_number}/{total_steps}] {step_name}{self.RESET}")
    
    def workflow_complete(self, message: str):
        """Print workflow completion message."""
        self.print_if_not_quiet(f"\n{self.GREEN}🎉 {message}{self.RESET}")

    def raw(self, msg: str) -> None:
        """Emit msg verbatim with flush=True, respecting --quiet."""
        if not self.quiet:
            with self._print_lock:
                print(msg, flush=True)

    def print_rollup_summary(self, summary):
        """
        Print rollup summary of workflow results.

        Args:
            summary: WorkflowSummary instance with scan results
        """
        self.header("SMBSeek Scan Summary")

        # Display query used
        if hasattr(summary, 'shodan_query') and summary.shodan_query:
            self.print_if_not_quiet(f"🌍 Shodan Query: {summary.shodan_query}")

        # Display core statistics
        self.print_if_not_quiet(f"📊 Hosts Scanned: {getattr(summary, 'hosts_scanned', 0)}")
        self.print_if_not_quiet(f"🔓 Hosts Accessible: {getattr(summary, 'hosts_accessible', 0)}")
        self.print_if_not_quiet(f"📁 Accessible Shares: {getattr(summary, 'accessible_shares', 0)}")

        # Display database location
        self.print_if_not_quiet(f"💾 Results saved to: smbseek.db")

        # Success indicator
        if getattr(summary, 'accessible_shares', 0) > 0:
            self.success(f"Scan completed: Found {summary.accessible_shares} accessible shares")
        elif getattr(summary, 'hosts_accessible', 0) > 0:
            self.success(f"Scan completed: {summary.hosts_accessible} hosts accessible (no enumerable shares)")
        else:
            self.info("Scan completed: No accessible SMB resources found")

            # Show cautious mode tip when no results found
            if getattr(summary, 'cautious_mode', False):
                self.print_if_not_quiet("")
                self.info("💡 Tip: Default mode may discover more hosts. Cautious mode prioritizes security over compatibility.")


class SMBSeekReporter:
    """
    Executive reporting functionality for SMBSeek.
    
    Generates executive summaries, detailed reports, and intelligence
    assessments from database data.
    """
    
    def __init__(self, database_manager, output_manager):
        """
        Initialize reporter.
        
        Args:
            database_manager: SMBSeekWorkflowDatabase instance
            output_manager: SMBSeekOutput instance
        """
        self.db = database_manager
        self.output = output_manager
    
    def generate_executive_summary(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate executive summary of SMB security findings.
        
        Args:
            session_id: Specific session to report on (optional)
            
        Returns:
            Executive summary dictionary
        """
        try:
            # Get overall statistics
            servers_query = """
                SELECT 
                    COUNT(*) as total_servers,
                    COUNT(CASE WHEN status = 'accessible' THEN 1 END) as accessible_servers,
                    COUNT(CASE WHEN scan_count > 0 THEN 1 END) as successfully_scanned
                FROM smb_servers
            """
            
            if session_id:
                servers_query += " WHERE last_session_id = ?"
                servers_stats = self.db.db_manager.execute_query(servers_query, (session_id,))
            else:
                servers_stats = self.db.db_manager.execute_query(servers_query)
            
            server_data = servers_stats[0] if servers_stats else {
                'total_servers': 0, 'accessible_servers': 0, 'successfully_scanned': 0
            }
            
            # Get share statistics
            shares_query = """
                SELECT 
                    COUNT(*) as total_shares,
                    COUNT(CASE WHEN accessible = 1 THEN 1 END) as accessible_shares,
                    COUNT(DISTINCT server_id) as servers_with_shares
                FROM share_access
            """
            
            if session_id:
                shares_query += " WHERE session_id = ?"
                shares_stats = self.db.db_manager.execute_query(shares_query, (session_id,))
            else:
                shares_stats = self.db.db_manager.execute_query(shares_query)
            
            share_data = shares_stats[0] if shares_stats else {
                'total_shares': 0, 'accessible_shares': 0, 'servers_with_shares': 0
            }
            
            # Get file statistics
            files_query = """
                SELECT 
                    COUNT(*) as total_files,
                    COUNT(CASE WHEN file_size > 0 THEN 1 END) as non_empty_files,
                    SUM(file_size) as total_size_bytes
                FROM file_manifests
            """
            
            if session_id:
                files_query += " WHERE session_id = ?"
                files_stats = self.db.db_manager.execute_query(files_query, (session_id,))
            else:
                files_stats = self.db.db_manager.execute_query(files_query)
            
            file_data = files_stats[0] if files_stats else {
                'total_files': 0, 'non_empty_files': 0, 'total_size_bytes': 0
            }
            
            # Get top countries
            countries_query = """
                SELECT country, COUNT(*) as count
                FROM smb_servers
                GROUP BY country
                ORDER BY count DESC
                LIMIT 5
            """
            
            if session_id:
                countries_query = """
                    SELECT country, COUNT(*) as count
                    FROM smb_servers
                    WHERE last_session_id = ?
                    GROUP BY country
                    ORDER BY count DESC
                    LIMIT 5
                """
                countries_stats = self.db.db_manager.execute_query(countries_query, (session_id,))
            else:
                countries_stats = self.db.db_manager.execute_query(countries_query)
            
            # Calculate risk indicators
            accessibility_rate = (
                (server_data['accessible_servers'] / server_data['total_servers'] * 100)
                if server_data['total_servers'] > 0 else 0
            )
            
            share_exposure_rate = (
                (share_data['accessible_shares'] / share_data['total_shares'] * 100)
                if share_data['total_shares'] > 0 else 0
            )
            
            # Risk assessment
            if accessibility_rate > 20:
                risk_level = "HIGH"
                risk_color = "red"
            elif accessibility_rate > 10:
                risk_level = "MEDIUM"
                risk_color = "yellow"
            else:
                risk_level = "LOW"
                risk_color = "green"
            
            return {
                'timestamp': datetime.now().isoformat(),
                'session_id': session_id,
                'summary': {
                    'total_servers_discovered': server_data['total_servers'],
                    'accessible_servers': server_data['accessible_servers'],
                    'accessibility_rate_percent': round(accessibility_rate, 1),
                    'total_shares_found': share_data['total_shares'],
                    'accessible_shares': share_data['accessible_shares'],
                    'share_exposure_rate_percent': round(share_exposure_rate, 1),
                    'total_files_enumerated': file_data['total_files'],
                    'total_data_size_mb': round((file_data['total_size_bytes'] or 0) / 1024 / 1024, 2)
                },
                'risk_assessment': {
                    'overall_risk_level': risk_level,
                    'risk_color': risk_color,
                    'key_findings': self._generate_key_findings(server_data, share_data, file_data),
                    'recommendations': self._generate_recommendations(accessibility_rate, share_exposure_rate)
                },
                'geographic_distribution': countries_stats,
                'technical_details': {
                    'servers_successfully_scanned': server_data['successfully_scanned'],
                    'servers_with_accessible_shares': share_data['servers_with_shares'],
                    'scan_coverage_percent': round(
                        (server_data['successfully_scanned'] / server_data['total_servers'] * 100)
                        if server_data['total_servers'] > 0 else 0, 1
                    )
                }
            }
        
        except Exception as e:
            self.output.error(f"Failed to generate executive summary: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'summary': {},
                'risk_assessment': {'overall_risk_level': 'UNKNOWN'},
                'geographic_distribution': [],
                'technical_details': {}
            }
    
    def _generate_key_findings(self, server_data: Dict, share_data: Dict, file_data: Dict) -> List[str]:
        """Generate key findings from data."""
        findings = []
        
        if server_data['accessible_servers'] > 0:
            findings.append(f"{server_data['accessible_servers']} servers allow unauthorized access")
        
        if share_data['accessible_shares'] > 0:
            findings.append(f"{share_data['accessible_shares']} network shares are publicly accessible")
        
        if file_data['total_files'] > 0:
            findings.append(f"{file_data['total_files']} files discovered on accessible shares")
        
        if not findings:
            findings.append("No significant security exposures detected")
        
        return findings
    
    def _generate_recommendations(self, accessibility_rate: float, share_exposure_rate: float) -> List[str]:
        """Generate security recommendations."""
        recommendations = []
        
        if accessibility_rate > 10:
            recommendations.append("Implement proper SMB authentication on exposed servers")
            recommendations.append("Review and restrict anonymous/guest access policies")
        
        if share_exposure_rate > 15:
            recommendations.append("Audit and restrict share permissions")
            recommendations.append("Remove unnecessary network shares")
        
        if accessibility_rate > 5 or share_exposure_rate > 5:
            recommendations.append("Implement network segmentation and access controls")
            recommendations.append("Regular security auditing of SMB infrastructure")
        
        if not recommendations:
            recommendations.append("Continue monitoring for new exposures")
            recommendations.append("Maintain current security posture")
        
        return recommendations
    
    def print_executive_summary(self, summary: Dict[str, Any]):
        """Print formatted executive summary to console."""
        self.output.header("SMBSeek Executive Summary")
        
        # Overview
        self.output.subheader("Security Overview")
        summary_data = summary.get('summary', {})
        risk_data = summary.get('risk_assessment', {})
        
        # Risk level with color
        risk_level = risk_data.get('overall_risk_level', 'UNKNOWN')
        if risk_data.get('risk_color') == 'red':
            risk_display = f"{self.output.RED}{risk_level}{self.output.RESET}"
        elif risk_data.get('risk_color') == 'yellow':
            risk_display = f"{self.output.YELLOW}{risk_level}{self.output.RESET}"
        else:
            risk_display = f"{self.output.GREEN}{risk_level}{self.output.RESET}"
        
        self.output.print_if_not_quiet(f"Overall Risk Level: {risk_display}")
        self.output.print_if_not_quiet(f"Servers Discovered: {summary_data.get('total_servers_discovered', 0)}")
        self.output.print_if_not_quiet(f"Accessible Servers: {summary_data.get('accessible_servers', 0)} ({summary_data.get('accessibility_rate_percent', 0)}%)")
        self.output.print_if_not_quiet(f"Accessible Shares: {summary_data.get('accessible_shares', 0)} ({summary_data.get('share_exposure_rate_percent', 0)}%)")
        self.output.print_if_not_quiet(f"Files Enumerated: {summary_data.get('total_files_enumerated', 0)}")
        
        # Key findings
        self.output.subheader("Key Findings")
        for finding in risk_data.get('key_findings', []):
            self.output.print_if_not_quiet(f"• {finding}")
        
        # Recommendations
        self.output.subheader("Recommendations")
        for recommendation in risk_data.get('recommendations', []):
            self.output.print_if_not_quiet(f"• {recommendation}")
        
        # Geographic distribution
        geo_data = summary.get('geographic_distribution', [])
        if geo_data:
            self.output.subheader("Geographic Distribution")
            for country_data in geo_data[:3]:  # Top 3
                self.output.print_if_not_quiet(f"• {country_data['country']}: {country_data['count']} servers")
        
        # Technical details
        tech_data = summary.get('technical_details', {})
        if tech_data:
            self.output.subheader("Technical Summary")
            self.output.print_if_not_quiet(f"Scan Coverage: {tech_data.get('scan_coverage_percent', 0)}%")
            self.output.print_if_not_quiet(f"Servers with Shares: {tech_data.get('servers_with_accessible_shares', 0)}")
    
    def save_detailed_report(self, summary: Dict[str, Any], filename: Optional[str] = None) -> str:
        """
        Save detailed report to JSON file.
        
        Args:
            summary: Executive summary data
            filename: Output filename (optional)
            
        Returns:
            Path to saved report file
        """
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"smbseek_report_{timestamp}.json"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, default=str)
            
            self.output.success(f"Detailed report saved: {filename}")
            return filename
        
        except Exception as e:
            self.output.error(f"Failed to save report: {e}")
            return ""


def create_output_manager(config, quiet: bool = False, verbose: bool = False, no_colors: bool = False) -> SMBSeekOutput:
    """
    Create output manager instance.
    
    Args:
        config: SMBSeekConfig instance
        quiet: Suppress output
        verbose: Enable verbose output
        no_colors: Disable colors
        
    Returns:
        SMBSeekOutput instance
    """
    return SMBSeekOutput(config, quiet, verbose, no_colors)


def create_reporter(database_manager, output_manager) -> SMBSeekReporter:
    """
    Create reporter instance.
    
    Args:
        database_manager: Database manager instance
        output_manager: Output manager instance
        
    Returns:
        SMBSeekReporter instance
    """
    return SMBSeekReporter(database_manager, output_manager)
