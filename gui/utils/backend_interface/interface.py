"""
SMBSeek Backend Interface

Provides subprocess wrapper for CLI commands with output parsing and progress tracking.
Implements complete backend isolation without any code modifications.

Design Decision: Use subprocess calls rather than direct imports to maintain
complete separation between GUI and backend teams' code.
"""

import subprocess
import sys
import threading
import queue
import json
import os
import re
import time
import signal
from contextlib import contextmanager
from typing import Dict, List, Optional, Callable, Tuple, Any
from pathlib import Path

from . import config
from . import process_runner
from . import progress
from . import mock_operations
from . import error_parser
from ..logging_config import get_logger

_logger = get_logger("backend_interface")


class BackendInterface:
    """
    Interface for communicating with SMBSeek backend via subprocess calls.

    Provides methods for executing CLI commands, parsing output, and tracking
    progress for long-running operations. All communication is through the
    existing CLI interface to avoid backend modifications.

    Design Pattern: Complete backend isolation with graceful error handling
    and user-friendly progress updates.
    """

    
    def __init__(self, backend_path: str = ".", mock_mode: bool = False):
        """
        Initialize backend interface.

        Args:
            backend_path: Path to SMBSeek installation directory
            mock_mode: Enable mock mode for testing without backend

        Design Decision: Default to current directory (.) for unified repo structure,
        but allow complete override for different deployment scenarios.
        """
        self.backend_path = Path(backend_path).resolve()
        self.cli_script = self.backend_path / "cli" / "smbseek.py"
        self.ftp_cli_script = self.backend_path / "cli" / "ftpseek.py"
        self.http_cli_script = self.backend_path / "cli" / "httpseek.py"
        self.config_path = self.backend_path / "conf" / "config.json"
        self.config_example_path = self.backend_path / "conf" / "config.json.example"

        # Mock mode for testing without backend
        self.mock_mode = mock_mode

        # Ensure configuration file exists (skip in mock mode)
        if not self.mock_mode:
            config.ensure_config_exists(self)
        
        # Progress tracking for long-running operations
        self.progress_queue = queue.Queue()
        self.current_operation = None

        # Cancellation tracking for subprocess operations
        self.active_process = None
        self.active_output_thread = None
        self.cancel_requested = False
        
        # Phase tracking with persistence for better progress accuracy
        self.last_known_phase = None
        self.phase_progression = ['discovery', 'authentication', 'access_testing']
        
        # Use gui directory for mock data (relative to where GUI components are)
        self.mock_data_path = Path(__file__).resolve().parents[2] / "test_data" / "mock_responses"
        
        # Timeout configuration - loaded from config with environment override support
        self.default_timeout = None  # No timeout by default
        self.enable_debug_timeouts = False
        
        # Recent filtering configuration - loaded from SMBSeek config
        self.default_recent_days = 90  # Default 90 days as per backend team recommendations
        
        # Load configuration and validate backend (skip in mock mode)
        if not self.mock_mode:
            config.load_timeout_configuration(self)
            config.load_workflow_configuration(self)
            config.cleanup_startup_locks(self)
            self._validate_backend()
        else:
            # Set mock defaults
            self.default_timeout = None
            self.enable_debug_timeouts = False
            self.default_recent_days = 90
    
    def _extract_error_details(self, full_output: str, cmd: List[str]) -> str:
        """Delegate to error_parser while preserving public API."""
        return error_parser.extract_error_details(full_output, cmd)
    
    def _validate_backend(self) -> None:
        """Delegate backend validation to config helper."""
        return config.validate_backend(self)

    def _build_script_command(self, script_path: Path, *args) -> List[str]:
        """Build a CLI command for a seeker entrypoint using the GUI interpreter."""
        # Determine Python interpreter (same as GUI for environment consistency)
        interpreter = sys.executable
        if not interpreter:
            # Fallback chain for robustness
            interpreter = 'python3'  # Unix/Linux standard

        cli_args = [str(arg) for arg in args]
        if "--config" not in cli_args:
            cli_args.extend(["--config", str(self.config_path)])

        command_list = [interpreter, str(script_path), *cli_args]
        if os.getenv("XSMBSEEK_DEBUG_SUBPROCESS"):
            # Log script and arg count only (avoid logging potential credentials)
            _logger.debug("CLI command: %s with %d args", script_path, len(args))
        return command_list

    def _build_cli_command(self, *args) -> List[str]:
        """Build CLI command with proper Python interpreter."""
        return self._build_script_command(self.cli_script, *args)

    def _build_ftp_cli_command(self, *args) -> List[str]:
        """Build CLI command for ftpseek using same interpreter as GUI."""
        return self._build_script_command(self.ftp_cli_script, *args)

    def _build_http_cli_command(self, *args) -> List[str]:
        """Build CLI command for httpseek using same interpreter as GUI."""
        return self._build_script_command(self.http_cli_script, *args)

    def _build_tool_command(self, script_name: str, *args) -> List[str]:
        """
        Build command for tools/ scripts with proper interpreter and path resolution.

        Args:
            script_name: Name of script in tools/ directory (e.g., "db_query.py")
            *args: Arguments to pass to the tool script

        Returns:
            Command list with interpreter, tool script path, and arguments
        """
        # Determine Python interpreter (same as GUI for environment consistency)
        interpreter = sys.executable
        if not interpreter:
            interpreter = 'python3'  # Unix/Linux standard

        # Build cross-platform path to tool script
        script_path = str(self.backend_path / "tools" / script_name)

        command_list = [interpreter, script_path, *args]
        if os.getenv("XSMBSEEK_DEBUG_SUBPROCESS"):
            # Log script and arg count only (avoid logging potential credentials)
            _logger.debug("Tool command: %s with %d args", script_name, len(args))
        return command_list

    def enable_mock_mode(self, mock_data_path: Optional[str] = None) -> None:
        """
        Enable mock mode for testing without real backend calls.
        
        Args:
            mock_data_path: Path to mock response files
            
        Design Decision: Mock mode allows safe GUI testing without requiring
        real Shodan API keys or network access.
        """
        self.mock_mode = True
        if mock_data_path:
            self.mock_data_path = Path(mock_data_path)
    
    def disable_mock_mode(self) -> None:
        """Disable mock mode and use real backend calls."""
        self.mock_mode = False
    
    # ===== TIMEOUT CONFIGURATION SYSTEM =====
    
    def _get_operation_timeout(self, timeout_override: Optional[int] = None) -> Optional[int]:
        """
        Resolve timeout for operation with override support.
        
        Args:
            timeout_override: Optional per-operation timeout override
            
        Returns:
            Resolved timeout in seconds, or None for no timeout
            
        Resolution order:
        1. timeout_override parameter (highest priority)
        2. self.default_timeout from config/env
        3. None (no timeout fallback)
        """
        if timeout_override is not None:
            return timeout_override
        
        return self.default_timeout
    
    def _format_timeout_duration(self, timeout_seconds: Optional[int]) -> str:
        """
        Format timeout duration for user-friendly error messages.
        
        Args:
            timeout_seconds: Timeout in seconds, or None
            
        Returns:
            Formatted duration string (e.g., "2 hours", "30 minutes", "never")
        """
        if timeout_seconds is None:
            return "never"
        
        if timeout_seconds < 60:
            return f"{timeout_seconds} seconds"
        elif timeout_seconds < 3600:
            minutes = timeout_seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            hours = timeout_seconds / 3600
            if hours == int(hours):
                return f"{int(hours)} hour{'s' if int(hours) != 1 else ''}"
            else:
                return f"{hours:.1f} hours"
    
    def get_timeout_info(self) -> Dict[str, Any]:
        """
        Get current timeout configuration information for debugging/verification.
        
        Returns:
            Dictionary with timeout configuration details
        """
        env_timeout = os.environ.get('SMBSEEK_GUI_TIMEOUT')
        
        return {
            'effective_timeout_seconds': self.default_timeout,
            'effective_timeout_display': self._format_timeout_duration(self.default_timeout),
            'environment_variable': env_timeout,
            'debug_timeouts_enabled': self.enable_debug_timeouts,
            'config_file_path': str(self.config_path),
            'config_file_exists': self.config_path.exists()
        }
    
    def run_scan(self, countries: List[str], progress_callback: Optional[Callable] = None,
                 log_callback: Optional[Callable[[str], None]] = None,
                 use_recent_filtering: bool = True, recent_days: Optional[int] = None,
                 additional_args: List[str] = None, filters: str = None,
                 verbose: bool = True) -> Dict:
        """
        Execute complete SMBSeek scan workflow.

        Args:
            countries: List of country codes to scan
            progress_callback: Function to call with progress updates
            use_recent_filtering: Whether to apply recent filtering (default True)
            recent_days: Days for recent filtering (None uses config default)
            additional_args: Additional CLI arguments to pass to the scan command
            filters: Custom Shodan filters string to append to query (raw syntax)
            verbose: Whether to pass --verbose to backend CLI

        Returns:
            Dictionary with scan results and statistics

        Implementation: Recent filtering is now controlled through configuration
        overrides (workflow.access_recent_hours) instead of CLI --recent flag.
        SMBSeek 3.x removed the --recent CLI option in favor of config-based control.
        """
        if self.mock_mode:
            return mock_operations.mock_scan_operation(countries, progress_callback)

        # Build base command and add verbose flag when requested
        cmd = self._build_cli_command()
        if verbose:
            cmd.append("--verbose")  # For detailed progress parsing

        # Only append --country when countries list is truthy per SMBSeek 3.0 requirements
        if countries:
            countries_str = ",".join(countries)
            cmd.extend(["--country", countries_str])

        # Add custom filters if provided
        if filters:
            cmd.extend(["--filter", filters])

        # Add any additional CLI arguments
        if additional_args:
            cmd.extend(additional_args)

        # Handle recent filtering through config overrides instead of CLI flags
        config_overrides = {}
        if not use_recent_filtering:
            # Disable recent filtering by setting access_recent_hours to 0
            config_overrides['workflow'] = {'access_recent_hours': 0}
        elif recent_days is not None:
            # Apply custom recent filtering value
            recent_hours = recent_days * 24
            config_overrides['workflow'] = {'access_recent_hours': recent_hours}

        # Execute with config overrides if needed
        if config_overrides:
            with self._temporary_config_override(config_overrides):
                return process_runner.execute_with_progress(
                    self,
                    cmd,
                    progress_callback,
                    log_callback=log_callback
                )
        else:
            # Use default config values (no override needed)
            return process_runner.execute_with_progress(
                self,
                cmd,
                progress_callback,
                log_callback=log_callback
            )

    def run_ftp_scan(
        self,
        countries: List[str],
        progress_callback: Optional[Callable] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        filters: str = None,
        verbose: bool = True,
    ) -> Dict:
        """
        Execute FTP scan workflow via ftpseek CLI subprocess.

        Args:
            countries: ISO country codes (empty list = global scan).
            progress_callback: Called with (percentage, message) during scan.
            log_callback: Called with raw stdout lines for log streaming.
            filters: Custom Shodan filters string to append to query (raw syntax).
            verbose: Pass --verbose for parseable progress output.

        Returns:
            Result dict with 'success' key and parsed summary stats.
        """
        if self.mock_mode:
            return mock_operations.mock_ftp_scan_operation(countries, progress_callback)

        cmd = self._build_ftp_cli_command()
        if verbose:
            cmd.append("--verbose")
        if countries:
            cmd.extend(["--country", ",".join(countries)])
        if filters:
            cmd.extend(["--filter", filters])

        return process_runner.execute_with_progress(
            self,
            cmd,
            progress_callback,
            log_callback=log_callback,
        )

    def run_http_scan(
        self,
        countries: List[str],
        progress_callback: Optional[Callable] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        filters: str = None,
        verbose: bool = True,
    ) -> Dict:
        """
        Execute HTTP scan workflow via httpseek CLI subprocess.

        Args:
            countries: ISO country codes (empty list = global scan).
            progress_callback: Called with (percentage, message) during scan.
            log_callback: Called with raw stdout lines for log streaming.
            filters: Custom Shodan filters string to append to query (raw syntax).
            verbose: Pass --verbose for parseable progress output.

        Returns:
            Result dict with 'success' key and parsed summary stats.
        """
        if self.mock_mode:
            return mock_operations.mock_http_scan_operation(countries, progress_callback)

        cmd = self._build_http_cli_command()
        if verbose:
            cmd.append("--verbose")
        if countries:
            cmd.extend(["--country", ",".join(countries)])
        if filters:
            cmd.extend(["--filter", filters])

        return process_runner.execute_with_progress(
            self,
            cmd,
            progress_callback,
            log_callback=log_callback,
        )

    def run_discover(self, countries: List[str], progress_callback: Optional[Callable] = None) -> Dict:
        """
        DEPRECATED: Discovery-only mode has been removed in SMBSeek 3.0.
        This method now redirects to the unified scan workflow (no subcommands).
        Will be removed in a future version.

        Args:
            countries: List of country codes to scan
            progress_callback: Function to call with progress updates

        Returns:
            Dictionary with scan results (same as run_scan)
        """
        import warnings
        warnings.warn(
            "run_discover() is deprecated. Use run_scan() instead. "
            "Discovery-only mode has been removed in SMBSeek 3.0.",
            DeprecationWarning,
            stacklevel=2
        )
        _logger.warning("DEPRECATED: run_discover() called - redirecting to unified workflow")
        return self.run_scan(countries, progress_callback)
    
    def run_access_verification(self, recent_days: Optional[int] = None,
                               progress_callback: Optional[Callable] = None) -> Dict:
        """
        Execute access verification operation with recent filtering.

        Args:
            recent_days: Filter to hosts from last N days (None uses config default)
            progress_callback: Function to call with progress updates

        Returns:
            Dictionary with access verification results

        Implementation: Recent filtering is now controlled through configuration
        overrides (workflow.access_recent_hours) instead of CLI --recent flag.
        SMBSeek 3.x removed the --recent CLI option in favor of config-based control.
        """
        if self.mock_mode:
            return mock_operations.mock_access_verification_operation(recent_days, progress_callback)

        # Use configured default if not specified
        if recent_days is None:
            recent_days = self.default_recent_days

        # Convert days to hours for config override
        recent_hours = recent_days * 24

        # Build base command without --recent flag
        cmd = self._build_cli_command("--verbose")

        # Apply recent filtering through config override
        config_overrides = {'workflow': {'access_recent_hours': recent_hours}}
        with self._temporary_config_override(config_overrides):
            return process_runner.execute_with_progress(self, cmd, progress_callback)
    
    def run_access_on_servers(self, ip_list: List[str], 
                             progress_callback: Optional[Callable] = None) -> Dict:
        """
        Execute access verification on specific servers.
        
        Args:
            ip_list: List of IP addresses to test
            progress_callback: Function to call with progress updates
            
        Returns:
            Dictionary with access verification results
        """
        if self.mock_mode:
            return mock_operations.mock_access_on_servers_operation(ip_list, progress_callback)
        
        if not ip_list:
            raise ValueError("Server list cannot be empty")
        
        servers_str = ",".join(ip_list)
        cmd = self._build_cli_command(
            "--servers", servers_str,
            "--verbose"
        )
        
        return process_runner.execute_with_progress(self, cmd, progress_callback)
    
    def should_skip_recent_scan(self, recent_days: Optional[int] = None) -> bool:
        """
        Check if recent scan makes new scan redundant.

        Args:
            recent_days: Days to check for recent activity (None uses config default)

        Returns:
            True if recent scan exists and new scan should be skipped

        Note: This method is currently unused and may be deprecated.
        It uses db_query.py tool with --recent flag, which may differ from
        the main SMBSeek CLI that removed --recent in 3.x. The main scan
        methods now use config-based recent filtering instead.
        """
        if recent_days is None:
            recent_days = self.default_recent_days
            
        try:
            # Query database for recent scan activity using tool script
            recent_hours = recent_days * 24
            cmd = self._build_tool_command(
                "db_query.py",
                "--recent", str(recent_hours),
                "--count-only"
            )
            
            result = subprocess.run(
                cmd,
                cwd=self.backend_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                # Parse count from output
                output = result.stdout.strip()
                try:
                    count = int(output)
                    return count > 0  # Skip if we have recent results
                except ValueError:
                    return False
            else:
                return False  # Error - don't skip, let scan proceed
                
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            return False  # Error - don't skip, let scan proceed
    
    def get_database_summary(self) -> Dict:
        """
        Get summary statistics from database.
        
        Returns:
            Dictionary with database statistics
            
        Design Decision: Use CLI query command rather than direct database
        access to maintain backend interface consistency.
        """
        if self.mock_mode:
            return {
                "total_servers": 7,
                "accessible_shares": 17,
                "vulnerabilities": 7,
                "recent_discoveries": {
                    "discovered": 24,
                    "accessible": 12,
                    "display": "24 / 12"
                }
            }
        
        cmd = self._build_tool_command("db_query.py", "--summary")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self.backend_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                return progress.parse_summary_output(result.stdout)
            else:
                raise subprocess.CalledProcessError(result.returncode, cmd, result.stderr)
                
        except subprocess.TimeoutExpired:
            raise TimeoutError("Database summary query timed out")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Database query failed: {e.stderr}")
    

    def run_initialization_scan(self, config_path: Optional[str] = None,
                               progress_callback: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
        """
        Run initialization scan to create a new database.
        
        Args:
            config_path: Path to configuration file 
            progress_callback: Function to call with progress updates (percentage, message)
            
        Returns:
            Dictionary with scan results and database path
        """
        if self.mock_mode:
            return mock_operations.mock_initialization_scan(progress_callback)
        
        try:
            if progress_callback:
                progress_callback(5, "Reading configuration...")
            
            # Read configuration to get scan parameters
            config = self._load_config(config_path)
            if not config:
                raise RuntimeError(f"Failed to load configuration from {config_path}")
            
            if progress_callback:
                progress_callback(10, f"Configuration loaded: {config_path}")
            
            # Extract scan parameters from config
            countries = config.get('countries', ['US'])  # Default to US if not specified
            if isinstance(countries, str):
                countries = [countries]
            
            if progress_callback:
                progress_callback(15, f"Starting scan for countries: {', '.join(countries)}")
            
            # Run the scan using existing run_scan method
            scan_result = self.run_scan(countries, progress_callback)
            
            # Determine database path
            db_path = os.path.join(self.backend_path, "smbseek.db")
            
            # Enhance result with database information
            result = {
                'success': scan_result.get('success', False),
                'database_path': db_path,
                'servers_found': scan_result.get('hosts_tested', 0),
                'scan_result': scan_result
            }
            
            if not result['success']:
                result['error'] = scan_result.get('error', 'Scan failed for unknown reason')
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'database_path': None
            }
    
    def _load_config(self, config_path: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Load configuration from file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Configuration dictionary or None if failed
        """
        if not config_path:
            config_path = os.path.join(self.backend_path, "conf", "config.json")
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    

    def terminate_current_operation(self, graceful: bool = False) -> None:
        """Terminate the currently running operation."""
        return process_runner.terminate_operation(self, graceful)

    def get_operation_status(self) -> Optional[Dict]:
        """
        Get status of current operation.
        
        Returns:
            Dictionary with operation status or None if no operation running
        """
        return self.current_operation
    
    def is_backend_available(self) -> bool:
        """
        Check if backend is available and functional.
        
        Returns:
            True if backend can be accessed, False otherwise
        """
        if self.mock_mode:
            return True
        
        try:
            result = subprocess.run(
                self._build_cli_command("--help"),
                cwd=self.backend_path,
                capture_output=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            return False
    
    def get_backend_version(self) -> Optional[str]:
        """
        Get backend version information.
        
        Returns:
            Version string or None if unavailable
        """
        if self.mock_mode:
            return "2.0.0 (mock)"
        
        try:
            result = subprocess.run(
                self._build_cli_command("--version"),
                cwd=self.backend_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # Extract version from output
                version_match = re.search(r'SMBSeek (\S+)', result.stdout)
                if version_match:
                    return version_match.group(1)
            return "Unknown"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    def load_effective_config(self) -> Dict[str, Any]:
        """
        Load the effective configuration with defaults for scan dialog.

        Returns:
            Dictionary with config values, including safe defaults
        """
        if self.mock_mode:
            return {
                'shodan': {
                    'api_key': 'mock_api_key_12345678901234567890123456789012',
                    'query_limits': {'max_results': 1000}
                },
                'workflow': {
                    'access_recent_hours': 2160  # 90 days * 24 hours
                }
            }

        config = {}
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            # Return safe defaults if config unavailable
            pass

        # Ensure required sections exist with defaults
        if 'shodan' not in config:
            config['shodan'] = {}
        if 'query_limits' not in config['shodan']:
            config['shodan']['query_limits'] = {}
        if 'max_results' not in config['shodan']['query_limits']:
            config['shodan']['query_limits']['max_results'] = 1000

        if 'workflow' not in config:
            config['workflow'] = {}
        if 'access_recent_hours' not in config['workflow']:
            # Convert from existing access_recent_days if available
            recent_days = config['workflow'].get('access_recent_days', 90)
            config['workflow']['access_recent_hours'] = recent_days * 24

        if 'connection' not in config or not isinstance(config['connection'], dict):
            config['connection'] = {}
        connection_config = config['connection']
        connection_config.setdefault('rate_limit_delay', 1)
        connection_config.setdefault('share_access_delay', 1)

        if 'discovery' not in config or not isinstance(config['discovery'], dict):
            config['discovery'] = {'max_concurrent_hosts': 1}
        else:
            config['discovery'].setdefault('max_concurrent_hosts', 1)

        if 'access' not in config or not isinstance(config['access'], dict):
            config['access'] = {'max_concurrent_hosts': 1}
        else:
            config['access'].setdefault('max_concurrent_hosts', 1)

        return config

    @contextmanager
    def _temporary_config_override(self, overrides: Dict[str, Any]):
        """
        Context manager for temporary config file with overrides.

        Args:
            overrides: Dictionary of config values to override

        Yields:
            Path to temporary config file

        Usage:
            with self._temporary_config_override({'shodan': {'api_key': 'new_key'}}):
                # Use temp config for subprocess calls
        """
        import tempfile

        # Load current config and apply overrides
        base_config = self.load_effective_config()

        # Deep merge overrides into base config
        def deep_merge(base: dict, override: dict) -> dict:
            """Deep merge two dictionaries."""
            result = base.copy()
            for key, value in override.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = deep_merge(result[key], value)
                else:
                    result[key] = value
            return result

        temp_config = deep_merge(base_config, overrides)

        # Create temporary file with proper cleanup
        fd = None
        temp_path = None
        original_config_path = self.config_path
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="smbseek_config_")

            # Write merged config to temp file
            with os.fdopen(fd, 'w') as f:
                json.dump(temp_config, f, indent=2)
                fd = None  # fdopen closes the descriptor

            # Temporarily point interface at the temp config
            self.config_path = Path(temp_path)
            yield temp_path

        finally:
            # Restore original config path
            self.config_path = original_config_path
            # Cleanup: close descriptor if still open, then remove file
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass  # Already closed

            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass  # Cleanup failed, but don't crash
