"""
SMBSeek Scan Manager

Manages SMB security scan operations with lock file coordination,
progress tracking, and graceful error handling.

Design Decision: Centralized scan management with lock file coordination
ensures single scan execution and proper resource cleanup.
"""

import json
import os
import subprocess
import threading
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Tuple
try:
    import psutil
except ImportError:
    # Fallback if psutil is not available
    psutil = None
import sys

from gui.utils.backend_interface import BackendInterface
from gui.utils.logging_config import get_logger

_logger = get_logger("scan_manager")


class ScanManager:
    """
    Manages SMB security scan operations.
    
    Handles:
    - Lock file management for single scan execution
    - Progress tracking and CLI output parsing
    - Error handling and recovery
    - Results collection and database integration
    - Graceful interruption handling
    
    Design Pattern: Singleton-style manager with comprehensive
    lifecycle management and error recovery capabilities.
    """
    
    def __init__(self, gui_directory: str = None, ui_dispatcher=None):
        """
        Initialize scan manager.

        Args:
            gui_directory: Path to GUI directory for lock files
            ui_dispatcher: Optional UIDispatcher for thread-safe UI callbacks.
                           If provided, progress callbacks are marshaled to
                           the main thread. If None, callbacks are invoked
                           directly (legacy behavior).
        """
        self.ui_dispatcher = ui_dispatcher

        if gui_directory:
            self.gui_dir = Path(gui_directory)
        else:
            self.gui_dir = Path(__file__).parent.parent
        
        self.lock_file = self.gui_dir / ".scan_lock"
        
        # Scan state
        self.current_scan = None
        self.scan_start_time = None
        self.scan_thread = None
        self.is_scanning = False
        
        # Backend interface
        self.backend_interface = None
        
        # Progress tracking
        self.progress_callback = None
        self.last_progress_update = None
        
        # Results tracking
        self.scan_results = {}
        self.log_callback = None
        
        # Clean up any stale lock files on startup
        self._cleanup_stale_locks()
        
        # Also initialize backend interface cleanup if backend_interface is available
        self._initialize_backend_lock_cleanup()
    
    def _process_exists(self, pid: int) -> bool:
        """
        Check if process with given PID exists.
        
        Args:
            pid: Process ID to check
            
        Returns:
            True if process exists, False otherwise
        """
        if psutil:
            return psutil.pid_exists(pid)
        else:
            # Fallback method using os
            try:
                os.kill(pid, 0)
                return True
            except (OSError, ProcessLookupError):
                return False
    
    def _cleanup_stale_locks(self) -> None:
        """Clean up stale lock files from previous sessions."""
        if self.lock_file.exists():
            try:
                # Read lock file metadata
                with open(self.lock_file, 'r') as f:
                    lock_data = json.load(f)
                
                # Check if process is still running
                pid = lock_data.get('process_id')
                if pid and self._process_exists(pid):
                    # Process still exists, lock is valid
                    return
                
                # Process doesn't exist, remove stale lock
                self.lock_file.unlink()
                
            except (json.JSONDecodeError, FileNotFoundError, KeyError):
                # Invalid or corrupted lock file, remove it
                if self.lock_file.exists():
                    self.lock_file.unlink()
    
    def _initialize_backend_lock_cleanup(self) -> None:
        """
        Initialize backend interface lock cleanup for coordination.
        
        Ensures that both GUI and backend lock files are cleaned up
        as recommended by backend team integration guidelines.
        """
        try:
            # Clean up any additional lock patterns that might exist
            lock_patterns = [
                ".scan_lock",
                ".access_lock", 
                ".discovery_lock",
                ".collection_lock"
            ]
            
            for pattern in lock_patterns:
                lock_path = self.gui_dir / pattern
                if lock_path.exists():
                    try:
                        # Check if lock file contains process information
                        with open(lock_path, 'r') as f:
                            lock_data = json.load(f)
                        
                        # Check if process is still running
                        pid = lock_data.get('process_id')
                        if pid and self._process_exists(pid):
                            # Process still exists, lock is valid
                            continue
                        
                        # Process doesn't exist, remove stale lock
                        lock_path.unlink()
                        
                    except (json.JSONDecodeError, FileNotFoundError, KeyError):
                        # Invalid or corrupted lock file, remove it
                        if lock_path.exists():
                            lock_path.unlink()
                            
        except Exception:
            # Non-critical cleanup failure - continue without error
            pass
    
    def is_scan_active(self) -> bool:
        """
        Check if a scan is currently active.
        
        Returns:
            True if scan is active, False otherwise
        """
        if self.is_scanning:
            return True
        
        if not self.lock_file.exists():
            return False
        
        try:
            with open(self.lock_file, 'r') as f:
                lock_data = json.load(f)
            
            # Check if process is still running
            pid = lock_data.get('process_id')
            if pid and self._process_exists(pid):
                return True
            else:
                # Stale lock file
                self.lock_file.unlink()
                return False
                
        except (json.JSONDecodeError, FileNotFoundError, KeyError):
            return False
    
    def create_lock_file(self, country: Optional[str] = None, scan_type: str = "complete") -> bool:
        """
        Create scan lock file with metadata.
        
        Args:
            country: Country code for scan (None for global)
            scan_type: Type of scan being performed
            
        Returns:
            True if lock created successfully, False if scan already active
        """
        if self.is_scan_active():
            return False
        
        lock_data = {
            "start_time": datetime.now().isoformat(),
            "scan_type": scan_type,
            "country": country,
            "process_id": os.getpid(),
            "created_by": "SMBSeek GUI"
        }
        
        try:
            with open(self.lock_file, 'w') as f:
                json.dump(lock_data, f, indent=2)
            return True
        except Exception:
            return False
    
    def remove_lock_file(self) -> None:
        """Remove scan lock file."""
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass
    
    def start_scan(self, scan_options: dict, backend_path: str,
                  progress_callback: Callable[[float, str, str], None],
                  log_callback: Optional[Callable[[str], None]] = None) -> bool:
        """
        Start a new SMB security scan with extended options.

        Args:
            scan_options: Dictionary containing scan configuration:
                - country: Country code (None for global scan)
                - max_shodan_results: Maximum Shodan results to fetch
                - recent_hours: Hours for recent filtering (None for default)
                - rescan_all: Whether to rescan all hosts
                - rescan_failed: Whether to rescan failed hosts
                - api_key_override: API key override (None for config default)
            backend_path: Path to backend directory
            progress_callback: Function called with (percentage, status, phase)
            log_callback: Function called with raw backend stdout lines for UI streaming

        Returns:
            True if scan started successfully, False otherwise
        """
        if self.is_scan_active():
            return False

        country = scan_options.get('country')

        # Create lock file
        if not self.create_lock_file(country, "complete"):
            return False

        try:
            # Initialize backend interface
            self.backend_interface = BackendInterface(backend_path)

            # Set up scan state
            self.is_scanning = True
            self.scan_start_time = datetime.now()
            self.progress_callback = progress_callback
            self.scan_results = {
                "start_time": self.scan_start_time.isoformat(),
                "country": country,
                "scan_options": scan_options,
                "status": "running"
            }
            self.log_callback = log_callback

            # Start scan in background thread with new options
            self.scan_thread = threading.Thread(
                target=self._scan_worker,
                args=(scan_options,),
                daemon=True
            )
            self.scan_thread.start()

            return True

        except Exception as e:
            # Clean up on error
            self.is_scanning = False
            self.remove_lock_file()
            self._update_progress(0, f"Failed to start scan: {str(e)}", "error")
            return False
    
    def _scan_worker(self, scan_options: dict) -> None:
        """
        Background worker thread for scan execution with extended options.

        Args:
            scan_options: Dictionary containing scan configuration
        """
        try:
            # Extract parameters
            country = scan_options.get('country')
            countries = [country] if country else []

            # Start scan
            self._update_progress(5, "Initializing scan...", "initialization")

            # Build config overrides
            config_overrides = {}

            # Apply max results override
            max_results = scan_options.get('max_shodan_results')
            if max_results is not None:
                config_overrides['shodan'] = {'query_limits': {'max_results': max_results}}

            # Apply API key override
            api_key = scan_options.get('api_key_override')
            if api_key:
                if 'shodan' not in config_overrides:
                    config_overrides['shodan'] = {}
                config_overrides['shodan']['api_key'] = api_key

            # Apply recent hours override (convert to access_recent_hours for config)
            recent_hours = scan_options.get('recent_hours')
            if recent_hours is not None:
                config_overrides['workflow'] = {'access_recent_hours': recent_hours}

            discovery_concurrency = scan_options.get('discovery_max_concurrent_hosts')
            if discovery_concurrency is not None:
                config_overrides.setdefault('discovery', {})['max_concurrent_hosts'] = discovery_concurrency

            access_concurrency = scan_options.get('access_max_concurrent_hosts')
            if access_concurrency is not None:
                config_overrides.setdefault('access', {})['max_concurrent_hosts'] = access_concurrency

            rate_limit_delay = scan_options.get('rate_limit_delay')
            if rate_limit_delay is not None:
                config_overrides.setdefault('connection', {})['rate_limit_delay'] = rate_limit_delay

            share_access_delay = scan_options.get('share_access_delay')
            if share_access_delay is not None:
                config_overrides.setdefault('connection', {})['share_access_delay'] = share_access_delay

            # Execute scan with temporary config override
            if config_overrides:
                self._update_progress(7, "Applying configuration overrides...", "initialization")
                with self.backend_interface._temporary_config_override(config_overrides):
                    results = self._execute_scan_with_options(countries, scan_options)
            else:
                results = self._execute_scan_with_options(countries, scan_options)

            # Process results
            self._process_scan_results(results)

        except Exception as e:
            # Handle scan errors
            self._handle_scan_error(e)
        finally:
            # Ensure cleanup happens regardless of success, failure, or cancellation
            self._cleanup_scan()

    def _execute_scan_with_options(self, countries: List[str], scan_options: dict) -> dict:
        """Execute scan with CLI options and config overrides."""
        # Build CLI arguments
        cli_args = []

        # Add CLI flags for rescan options
        if scan_options.get('rescan_all'):
            cli_args.append('--rescan-all')

        if scan_options.get('rescan_failed'):
            cli_args.append('--rescan-failed')

        security_mode = (scan_options.get('security_mode') or 'cautious').lower()
        if security_mode == 'legacy':
            cli_args.append('--legacy')

        verbose_flag = bool(scan_options.get('verbose', False))

        # Recent hours filtering is now handled through config overrides
        # in the _execute_scan method via _temporary_config_override
        # (lines 324-327). CLI --recent flag removed for SMBSeek 3.x compatibility.

        # Execute scan with additional CLI arguments
        self._update_progress(10, "Starting scan execution...", "discovery")

        # Use the backend interface run_scan method but with enhanced parameters
        # Extract custom filters from scan options
        custom_filters = scan_options.get('custom_filters', '')

        return self.backend_interface.run_scan(
            countries,
            progress_callback=self._handle_backend_progress,
            log_callback=self._handle_backend_log_line,
            additional_args=cli_args,
            filters=custom_filters,
            verbose=verbose_flag
        )

    def _handle_backend_log_line(self, line: str) -> None:
        """Forward raw backend stdout lines to the registered log callback."""
        if not self.log_callback:
            return

        try:
            self.log_callback(line)
        except Exception:
            # Swallow logging errors to avoid crashing scan threads
            pass
    
    def _handle_backend_progress(self, percentage: float, message: str) -> None:
        """
        Handle progress updates from backend interface.

        Backend interface already does sophisticated parsing, so we trust its
        percentage calculations and add minimal processing for phase detection.

        Args:
            percentage: Progress percentage (0-100) from backend interface
            message: Progress message from backend interface
        """
        # Handle message-only updates where percentage is None
        if percentage is None:
            percentage = float(self.last_progress_update.get("percentage", 0)) if hasattr(self, 'last_progress_update') and self.last_progress_update else 0.0

        # Ensure progress always moves forward (prevent stuck states)
        if hasattr(self, 'last_progress_update') and self.last_progress_update:
            last_percentage = self.last_progress_update.get("percentage", 0)
            # Only use new percentage if it's higher, or if significant time has passed
            # Skip comparison if either value is not numeric
            if isinstance(percentage, (int, float)) and isinstance(last_percentage, (int, float)) and percentage < last_percentage:
                import time
                last_time = self.last_progress_update.get("timestamp", "")
                current_time = datetime.now().isoformat()
                if last_time and (datetime.now() - datetime.fromisoformat(last_time)).total_seconds() > 30:
                    # Force progress increment if stuck for more than 30 seconds
                    percentage = min(last_percentage + 1, 100)
                else:
                    # Use the higher percentage to prevent going backwards
                    percentage = last_percentage
        
        # Simple phase detection based on message content (don't re-parse extensively)
        phase = self._detect_scan_phase(message)
        
        # Enhance message with activity indicators for better user feedback
        enhanced_message = self._enhance_progress_message(message, percentage, phase)
        
        # Update progress with enhanced information
        self._update_progress(percentage, enhanced_message, phase)
        
        # Store last update with timestamp
        self.last_progress_update = {
            "percentage": percentage,
            "message": enhanced_message,
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
            "backend_message": message  # Store original for debugging
        }
    
    def _detect_scan_phase(self, message: str) -> str:
        """
        Simple phase detection from backend message.
        
        Backend interface has already done sophisticated parsing, so we only
        need basic phase detection for UI display purposes.
        
        Args:
            message: Progress message from backend interface
            
        Returns:
            Detected phase name
        """
        message_lower = message.lower()
        
        # Simple keyword-based phase detection (SMBSeek 3.0 three-phase model)
        if any(keyword in message_lower for keyword in ['complete', 'finished', 'done']):
            return "completed"

        scoreboard_message = (
            ("testing hosts" in message_lower or "testing recent hosts" in message_lower)
            and ("success:" in message_lower or "failed:" in message_lower)
        ) or "auth results" in message_lower
        if scoreboard_message:
            return "access_testing"

        error_indicators = [
            'error:', ' error', 'critical', 'fatal', 'exception', 'traceback',
            'scan failed', 'failed to', 'failed due', 'failure', ' aborted', ' terminated'
        ]
        if any(indicator in message_lower for indicator in error_indicators):
            return "error"

        if any(keyword in message_lower for keyword in ['auth', 'access', 'testing', 'login', 'shares', 'enum']):
            return "access_testing"  # Combined access testing and enumeration
        elif any(keyword in message_lower for keyword in ['discover', 'shodan', 'query', 'search']):
            return "discovery"
        elif any(keyword in message_lower for keyword in ['initializ', 'start', 'begin']):
            return "initialization"
        else:
            return "scanning"  # Default fallback
    
    def _enhance_progress_message(self, message: str, percentage: float, phase: str) -> str:
        """
        Enhance progress message with additional context and user feedback.
        
        Args:
            message: Original message from backend interface
            percentage: Current progress percentage
            phase: Detected scan phase
            
        Returns:
            Enhanced message for better user experience
        """
        # Add activity indicator to show system is working
        activity_indicators = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        indicator_index = int((percentage // 2) % len(activity_indicators))
        activity_indicator = activity_indicators[indicator_index]
        
        # Add phase-specific prefixes for clarity (SMBSeek 3.0 three-phase model)
        phase_prefixes = {
            "initialization": "🚀 Starting",
            "discovery": "🔍 Discovering",
            "authentication": "🔐 Testing Authentication",
            "access_testing": "⚡ Testing Access",
            "completed": "✅ Complete",
            "error": "❌ Error",
            "scanning": "⚡ Scanning"
        }
        
        prefix = phase_prefixes.get(phase, "⚡ Processing")
        
        # Enhance message with context
        if percentage < 100 and phase not in ["completed", "error"]:
            # Add activity indicator and percentage for active scans
            enhanced = f"{activity_indicator} {prefix}: {message} ({percentage:.0f}%)"
        else:
            # Simpler format for completed/error states
            enhanced = f"{prefix}: {message}"
        
        # Add time-based activity for very long phases
        if hasattr(self, 'last_progress_update') and self.last_progress_update:
            last_time = self.last_progress_update.get("timestamp")
            if last_time:
                time_diff = (datetime.now() - datetime.fromisoformat(last_time)).total_seconds()
                if time_diff > 60 and percentage < 100:  # More than 1 minute in same phase
                    enhanced += f" (running {time_diff/60:.0f}m)"
        
        return enhanced
    
    def _update_progress(self, percentage: float, status: str, phase: str) -> None:
        """
        Update scan progress and notify callback.

        Args:
            percentage: Progress percentage (0-100)
            status: Status message
            phase: Current scan phase
        """
        if self.progress_callback:
            if self.ui_dispatcher:
                # Thread-safe: marshal callback to main thread
                self.ui_dispatcher.schedule(
                    self.progress_callback, percentage, status, phase
                )
            else:
                # Legacy fallback (gui/main.py path until WP2)
                self.progress_callback(percentage, status, phase)
    
    def _process_scan_results(self, results: Dict[str, Any]) -> None:
        """
        Process scan results and update scan state.
        
        Args:
            results: Results dictionary from backend interface
        """
        end_time = datetime.now()
        duration = end_time - self.scan_start_time
        
        # Check for cancellation first
        if results.get("cancelled", False):
            # Handle cancelled scan with enhanced mapping
            hosts_scanned = (results.get("hosts_scanned", 0) or
                            results.get("hosts_tested", 0) or 0)

            accessible_hosts = (results.get("hosts_accessible", 0) or
                               results.get("successful_auth", 0) or 0)

            shares_found = (results.get("accessible_shares", 0) or
                           results.get("shares_discovered", 0) or 0)

            self.scan_results.update({
                "end_time": end_time.isoformat(),
                "duration_seconds": duration.total_seconds(),
                "status": "cancelled",
                "success": False,  # Cancelled scans are not successful
                "backend_results": results,
                "hosts_scanned": hosts_scanned,
                "accessible_hosts": accessible_hosts,
                "shares_found": shares_found,
                "summary_message": "Scan cancelled by user"
            })

            # Update progress with cancellation message using dedicated phase
            self._update_progress(
                self.last_progress_update.get("percentage", 0) if self.last_progress_update else 50,
                "Scan cancelled by user",
                "cancelled"
            )
            return

        # Update scan results for normal completion/failure with enhanced mapping
        # Use multiple possible field names to ensure compatibility with different SMBSeek versions
        hosts_scanned = (results.get("hosts_scanned", 0) or
                        results.get("hosts_tested", 0) or 0)

        accessible_hosts = (results.get("hosts_accessible", 0) or
                           results.get("successful_auth", 0) or 0)

        shares_found = (results.get("accessible_shares", 0) or
                       results.get("shares_discovered", 0) or 0)

        # Add fallback logic only for successful scans with missing numbers.
        # If an error was returned (e.g., Shodan API failure), do NOT fallback to prior DB data.
        used_fallback = False
        if (results.get("success", False) and not results.get("error")
                and hosts_scanned == 0 and accessible_hosts == 0 and shares_found == 0
                and self.scan_results.get("protocol") != "ftp"):
            _logger.warning("CLI parsing returned zero values for all statistics. Attempting database fallback.")
            try:
                # Try to get recent statistics from database as fallback
                fallback_stats = self._get_recent_scan_stats_from_db()
                if fallback_stats:
                    hosts_scanned = fallback_stats.get("hosts_scanned", 0)
                    accessible_hosts = fallback_stats.get("accessible_hosts", 0)
                    shares_found = fallback_stats.get("shares_found", 0)
                    used_fallback = True
                    _logger.info("Database fallback successful: hosts=%d, accessible=%d, shares=%d",
                                hosts_scanned, accessible_hosts, shares_found)
                else:
                    _logger.warning("Database fallback returned no data.")
            except Exception as e:
                # Database fallback failed - continue with parsed values (likely 0)
                _logger.warning("Database fallback failed: %s", e)
                pass

        self.scan_results.update({
            "end_time": end_time.isoformat(),
            "duration_seconds": duration.total_seconds(),
            "status": "completed" if results.get("success", False) else "failed",
            "success": results.get("success", False),  # Propagate success to top-level for dashboard
            "backend_results": results,
            "hosts_scanned": hosts_scanned,
            "accessible_hosts": accessible_hosts,
            "shares_found": shares_found,
            # Add flag indicating if fallback was used
            "used_database_fallback": used_fallback
        })

        # Final progress update
        if results.get("success", False):
            hosts = self.scan_results["hosts_scanned"]
            accessible = self.scan_results["accessible_hosts"]

            # Build enhanced summary message (SMBSeek 3.0 - no file collection)
            summary_message = f"Scan completed: {accessible}/{hosts} hosts accessible"

            # Add note if database fallback was used for statistics
            if used_fallback:
                summary_message += " (statistics from database)"

            # Store summary message for UI display
            self.scan_results["summary_message"] = summary_message

            self._update_progress(
                100,
                summary_message,
                "completed"
            )
        else:
            error_msg = results.get("error", "Unknown error")
            self._update_progress(
                self.last_progress_update.get("percentage", 0) if self.last_progress_update else 0,
                f"Scan failed: {error_msg}",
                "error"
            )
    
    def _handle_scan_error(self, error: Exception) -> None:
        """
        Handle scan errors gracefully.
        
        Args:
            error: Exception that occurred during scan
        """
        end_time = datetime.now()
        
        if self.scan_start_time:
            duration = end_time - self.scan_start_time
            duration_seconds = duration.total_seconds()
        else:
            duration_seconds = 0
        
        # Update scan results with error information
        self.scan_results.update({
            "end_time": end_time.isoformat(),
            "duration_seconds": duration_seconds,
            "status": "error",
            "error": str(error),
            "error_type": type(error).__name__
        })
        
        # Determine which phase we were in
        current_phase = "unknown"
        if self.last_progress_update:
            current_phase = self.last_progress_update.get("phase", "unknown")
        
        # Update progress with error
        progress_percentage = 0
        if self.last_progress_update:
            progress_percentage = self.last_progress_update.get("percentage", 0)
        
        self._update_progress(
            progress_percentage,
            f"Scan interrupted in {current_phase} phase: {str(error)}",
            "error"
        )
    
    def _cleanup_scan(self) -> None:
        """Clean up after scan completion or failure."""
        self.is_scanning = False
        self.remove_lock_file()
        self.log_callback = None
        
        # Store final scan timestamp for results filtering
        if self.scan_results:
            self.scan_results["cleanup_time"] = datetime.now().isoformat()
    
    def get_scan_results(self) -> Dict[str, Any]:
        """
        Get current scan results.
        
        Returns:
            Dictionary with scan results and statistics
        """
        return self.scan_results.copy()
    
    def interrupt_scan(self) -> bool:
        """
        Interrupt currently running scan by terminating backend subprocess.

        Returns:
            True if cancellation was initiated, False if no scan active or error
        """
        if not self.is_scanning:
            return False

        try:
            # Update status to indicate cancellation in progress
            self.scan_results.update({
                "status": "cancelling",
                "cancellation_start": datetime.now().isoformat()
            })

            # Terminate the backend subprocess and its children
            if self.backend_interface:
                self.backend_interface.terminate_current_operation()

            # Keep is_scanning = True - let _cleanup_scan() reset it
            # This prevents dashboard monitor from bailing out early

            return True

        except Exception as e:
            # Log error but don't expose details to UI
            _logger.error("Error during scan cancellation: %s", e)
            return False
    
    def get_last_scan_time(self) -> Optional[datetime]:
        """
        Get timestamp of last completed scan for filtering.
        
        Returns:
            Datetime of last scan completion or None if no scans
        """
        if not self.scan_results or not self.scan_results.get("end_time"):
            return None
        
        try:
            return datetime.fromisoformat(self.scan_results["end_time"])
        except (ValueError, TypeError):
            return None

    def _get_recent_scan_stats_from_db(self) -> Optional[Dict[str, int]]:
        """
        Get recent scan statistics from database as fallback when parsing fails.

        Returns:
            Dictionary with hosts_scanned, accessible_hosts, shares_found or None if unavailable
        """
        try:
            # Import here to avoid circular dependencies
            from gui.utils.database_access import DatabaseReader

            # Try to get backend path for database location
            backend_path = getattr(self.backend_interface, 'backend_path', None)
            if not backend_path:
                return None

            db_path = os.path.join(backend_path, "smbseek.db")
            if not os.path.exists(db_path):
                return None

            # Create database reader and get recent statistics
            db_reader = DatabaseReader(str(db_path))
            if not db_reader.is_database_available():
                return None

            # Get dashboard summary which includes recent scan stats
            summary = db_reader.get_dashboard_summary()

            # Extract stats and calculate reasonable estimates
            recent_discoveries = summary.get("recent_discoveries", {})
            total_servers = summary.get("total_servers", 0)
            accessible_shares = summary.get("accessible_shares", 0)

            # If we have recent discovery data, use it
            if isinstance(recent_discoveries, dict):
                discovered = recent_discoveries.get("discovered", 0)
                accessible = recent_discoveries.get("accessible", 0)

                if discovered > 0 or accessible > 0:
                    return {
                        "hosts_scanned": discovered,
                        "accessible_hosts": accessible,
                        "shares_found": accessible_shares  # Use total accessible shares as estimate
                    }

            # Fallback: use total database stats if recent data unavailable
            if total_servers > 0:
                return {
                    "hosts_scanned": total_servers,
                    "accessible_hosts": summary.get("servers_with_accessible_shares", 0),
                    "shares_found": accessible_shares
                }

            return None

        except Exception:
            # Any error in database fallback should not crash the scan
            return None

    def start_ftp_scan(
        self,
        scan_options: dict,
        backend_path: str,
        progress_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Start an FTP scan in a background thread.

        Shares the same lock/state mechanism as start_scan() so only one
        protocol scan can run at a time. SMB behaviour is unchanged.

        Args:
            scan_options: Dict with optional 'country' key.
            backend_path: Path to SMBSeek installation directory.
            progress_callback: Called with (percentage, status, phase).
            log_callback: Called with raw stdout lines for log streaming.

        Returns:
            True if scan started, False if already scanning or lock failed.
        """
        if self.is_scan_active():
            return False

        country = scan_options.get("country")
        if not self.create_lock_file(country, "ftp"):
            return False

        try:
            self.backend_interface = BackendInterface(backend_path)
            self.is_scanning = True
            self.scan_start_time = datetime.now()
            self.progress_callback = progress_callback
            self.log_callback = log_callback
            self.scan_results = {
                "start_time": self.scan_start_time.isoformat(),
                "country": country,
                "scan_options": scan_options,
                "status": "running",
                "protocol": "ftp",
            }

            self.scan_thread = threading.Thread(
                target=self._ftp_scan_worker,
                args=(scan_options,),
                daemon=True,
            )
            self.scan_thread.start()
            return True

        except Exception as exc:
            self.is_scanning = False
            self.remove_lock_file()
            self._update_progress(0, f"Failed to start FTP scan: {exc}", "error")
            return False

    def _ftp_scan_worker(self, scan_options: dict) -> None:
        """
        Worker thread for FTP scan execution.

        Mirrors _scan_worker() structure exactly:
        - try: build config_overrides from scan_options, execute under
               _temporary_config_override (if any), _process_scan_results()
        - except: _handle_scan_error()
        - finally: _cleanup_scan() — always runs

        Progress updates go through _update_progress() for thread-safe UI
        dispatch via ui_dispatcher.
        """
        try:
            country_raw = scan_options.get("country") or ""
            countries = [c.strip() for c in country_raw.split(",") if c.strip()]

            self._update_progress(5, "Initializing FTP scan...", "initialization")

            # Build runtime config overrides from dialog options.
            config_overrides = {}

            # Shodan API key (shared global path, same as SMB).
            api_key = scan_options.get("api_key_override")
            if api_key:
                config_overrides["shodan"] = {"api_key": api_key}

            # FTP Shodan query limits.
            max_results = scan_options.get("max_shodan_results")
            if max_results is not None:
                (config_overrides
                 .setdefault("ftp", {})
                 .setdefault("shodan", {})
                 .setdefault("query_limits", {})
                 )["max_results"] = max_results

            # FTP discovery concurrency (key matches SMB naming convention).
            disc_conc = scan_options.get("discovery_max_concurrent_hosts")
            if disc_conc is not None:
                config_overrides.setdefault("ftp", {}).setdefault("discovery", {})[
                    "max_concurrent_hosts"
                ] = disc_conc

            # FTP access concurrency.
            acc_conc = scan_options.get("access_max_concurrent_hosts")
            if acc_conc is not None:
                config_overrides.setdefault("ftp", {}).setdefault("access", {})[
                    "max_concurrent_hosts"
                ] = acc_conc

            # FTP timeouts.
            verif_overrides = {}
            for key in ("connect_timeout", "auth_timeout", "listing_timeout"):
                val = scan_options.get(key)
                if val is not None:
                    verif_overrides[key] = val
            if verif_overrides:
                config_overrides.setdefault("ftp", {})["verification"] = verif_overrides

            verbose = bool(scan_options.get("verbose", False))

            if config_overrides:
                self._update_progress(7, "Applying configuration overrides...", "initialization")
                with self.backend_interface._temporary_config_override(config_overrides):
                    result = self.backend_interface.run_ftp_scan(
                        countries=countries,
                        progress_callback=self._handle_backend_progress,
                        log_callback=self._handle_backend_log_line,
                        verbose=verbose,
                    )
            else:
                result = self.backend_interface.run_ftp_scan(
                    countries=countries,
                    progress_callback=self._handle_backend_progress,
                    log_callback=self._handle_backend_log_line,
                    verbose=verbose,
                )

            self._process_scan_results(result)

        except Exception as exc:
            self._handle_scan_error(exc)

        finally:
            self._cleanup_scan()


# Global scan manager instance
_scan_manager = None


def get_scan_manager(gui_directory: str = None, ui_dispatcher=None) -> ScanManager:
    """
    Get global scan manager instance.

    Args:
        gui_directory: Path to GUI directory for lock files
        ui_dispatcher: Optional UIDispatcher for thread-safe UI callbacks.
                       Only used on first call (when creating the instance).

    Returns:
        ScanManager instance
    """
    global _scan_manager
    if _scan_manager is None:
        _scan_manager = ScanManager(gui_directory, ui_dispatcher=ui_dispatcher)
    return _scan_manager
