"""
Process execution logic for BackendInterface.

Contains methods for executing commands with progress tracking, parsing output streams,
and handling error recovery scenarios.
"""

import subprocess
import sys
import threading
import os
import time
import signal
from typing import Dict, List, Optional, Callable

from . import config
from . import progress
from ..logging_config import get_logger

_logger = get_logger("backend_interface.process_runner")


def execute_with_progress(interface, cmd: List[str],
                         progress_callback: Optional[Callable],
                         log_callback: Optional[Callable[[str], None]] = None,
                         timeout_override: Optional[int] = None) -> Dict:
    """
    Execute command with real-time progress tracking and configurable timeout.

    Args:
        interface: BackendInterface instance
        cmd: Command list for subprocess
        progress_callback: Function to call with (percentage, message) updates
        log_callback: Function to call with raw stdout lines (including ANSI codes)
        timeout_override: Optional timeout override in seconds (None = use config default)

    Returns:
        Dictionary with execution results

    Raises:
        TimeoutError: If operation exceeds configured timeout

    Implementation: Uses threading to capture output in real-time and
    parse progress indicators from CLI output. Timeout is configurable
    via config file, environment variable, or method parameter.
    """
    interface.current_operation = {
        "command": " ".join(cmd),
        "start_time": time.time(),
        "status": "running"
    }

    try:
        # Validate configuration before starting subprocess
        config_validation = config.validate_config(interface)
        if not config_validation["valid"]:
            raise RuntimeError(f"Configuration validation failed: {'; '.join(config_validation['errors'])}")

        # Start subprocess with pipes for real-time output
        # Force unbuffered output for immediate progress updates
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'  # Force Python unbuffered output

        # Ensure Python path includes current directory for imports
        if 'PYTHONPATH' in env:
            env['PYTHONPATH'] = f"{interface.backend_path}:{env['PYTHONPATH']}"
        else:
            env['PYTHONPATH'] = str(interface.backend_path)

        # Platform-specific process group creation for proper cancellation
        if sys.platform.startswith('win'):
            # Windows: Create new process group to allow terminating children
            process = subprocess.Popen(
                cmd,
                cwd=interface.backend_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # Unbuffered for real-time output
                universal_newlines=True,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            # POSIX: Start new session to create process group
            process = subprocess.Popen(
                cmd,
                cwd=interface.backend_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # Unbuffered for real-time output
                universal_newlines=True,
                env=env,
                start_new_session=True
            )

        # Thread for reading output and parsing progress
        output_lines = []
        progress_thread = threading.Thread(
            target=progress.parse_output_stream,
            args=(interface, process.stdout, output_lines, progress_callback, log_callback)
        )
        progress_thread.start()

        # Track active process and thread for cancellation
        interface.active_process = process
        interface.active_output_thread = progress_thread

        # Wait for completion with configurable timeout
        operation_timeout = interface._get_operation_timeout(timeout_override)

        # Debug logging for timeout resolution
        if interface.enable_debug_timeouts:
            timeout_source = "override" if timeout_override else "config/env"
            timeout_display = interface._format_timeout_duration(operation_timeout)
            _logger.debug("Using timeout: %s (source: %s)", timeout_display, timeout_source)

        try:
            returncode = process.wait(timeout=operation_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            timeout_duration = interface._format_timeout_duration(operation_timeout)
            cmd_str = " ".join(cmd[:3])  # First 3 command parts for context
            raise TimeoutError(f"Operation '{cmd_str}...' timed out after {timeout_duration}")

        progress_thread.join()

        # Check for cancellation before processing results
        if interface.cancel_requested:
            # Operation was cancelled - return cancellation result
            return {
                "success": False,
                "cancelled": True,
                "error": "Scan cancelled by user"
            }

        # Parse final results for normal completion
        full_output = "\n".join(output_lines)
        if os.getenv("XSMBSEEK_DEBUG_SUBPROCESS") or os.getenv("DIRRACUDA_DEBUG_SUBPROCESS"):
            _logger.debug("CLI output start")
            _logger.debug("%s", full_output)
            _logger.debug("CLI output end")
        results = progress.parse_final_results(full_output)

        interface.current_operation["status"] = "completed" if returncode == 0 else "failed"
        interface.current_operation["end_time"] = time.time()

        if returncode != 0:
            # Extract meaningful error message from output
            error_details = interface._extract_error_details(full_output, cmd)

            # Handle specific error cases with automatic recovery
            if error_details.startswith("RECENT_HOSTS_ERROR:"):
                # No recent hosts found - attempt automatic discovery as fallback
                return handle_no_recent_hosts_error(interface, cmd, error_details, progress_callback)
            elif error_details.startswith("SERVERS_NOT_AUTHENTICATED:"):
                # Specified servers not authenticated - suggest discovery
                return handle_servers_not_authenticated_error(interface, cmd, error_details)
            elif error_details.startswith("DEPENDENCY_MISSING:"):
                _, _, friendly_message = error_details.partition(":")
                raise RuntimeError(friendly_message.strip())
            else:
                # Regular error - no automatic recovery
                raise subprocess.CalledProcessError(returncode, cmd, error_details)

        return results

    except Exception as e:
        interface.current_operation["status"] = "failed"
        interface.current_operation["error"] = str(e)
        raise
    finally:
        # Clean up cancellation state after operation completes/fails/is cancelled
        # Ensure thread is joined and state is reset
        if progress_thread.is_alive():
            try:
                progress_thread.join(timeout=5)
            except (threading.ThreadError, RuntimeError):
                pass

        # Reset cancellation tracking state
        interface.active_process = None
        interface.active_output_thread = None
        interface.cancel_requested = False


def terminate_operation(interface, graceful: bool = False) -> None:
    """
    Terminate the currently running operation by killing the subprocess and its children.

    Args:
        interface: BackendInterface instance
        graceful: Whether to attempt graceful termination first (currently unused)
    """
    if interface.mock_mode:
        return

    if interface.active_process is None:
        return

    interface.cancel_requested = True

    try:
        process = interface.active_process

        if sys.platform.startswith('win'):
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
            except (ProcessLookupError, PermissionError, OSError):
                pass
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        try:
            if process.stdout and not process.stdout.closed:
                process.stdout.close()
        except (AttributeError, OSError):
            pass

        if interface.active_output_thread is not None:
            try:
                interface.active_output_thread.join(timeout=3)
            except (threading.ThreadError, RuntimeError):
                pass
            interface.active_output_thread = None

        if interface.current_operation:
            interface.current_operation.update({
                "status": "cancelled",
                "end_time": time.time()
            })

    except Exception as e:
        _logger.warning("Error during operation termination: %s", e)


def handle_no_recent_hosts_error(interface, original_cmd: List[str], error_details: str,
                                progress_callback: Optional[Callable]) -> Dict:
    """
    Handle 'no recent hosts found' error with automatic discovery fallback.

    Args:
        interface: BackendInterface instance
        original_cmd: The original command that failed
        error_details: Error details from CLI output
        progress_callback: Progress callback for discovery operation

    Returns:
        Dictionary with discovery results or error information
    """
    try:
        if progress_callback:
            progress_callback(0, "No recent hosts found - running discovery first...")

        # Extract countries from original command if available
        countries = []
        if "--country" in original_cmd:
            country_index = original_cmd.index("--country")
            if country_index + 1 < len(original_cmd):
                countries_str = original_cmd[country_index + 1]
                countries = countries_str.split(",")

        # If no countries found, default to US
        if not countries:
            countries = ["US"]

        # Run discovery operation
        discovery_result = interface.run_discover(countries, progress_callback)

        # Return discovery result with indication of fallback
        discovery_result["fallback_reason"] = "no_recent_hosts"
        discovery_result["original_error"] = error_details
        discovery_result["automatic_discovery"] = True

        return discovery_result

    except Exception as e:
        # Discovery also failed - return combined error information
        return {
            "success": False,
            "error": f"Automatic discovery fallback failed: {str(e)}",
            "original_error": error_details,
            "fallback_attempted": True
        }


def handle_servers_not_authenticated_error(interface, original_cmd: List[str], error_details: str) -> Dict:
    """
    Handle 'servers not authenticated' error.

    Args:
        interface: BackendInterface instance
        original_cmd: The original command that failed
        error_details: Error details from CLI output

    Returns:
        Dictionary with error information and suggested recovery
    """
    # Extract server list from command if available
    servers = []
    if "--servers" in original_cmd:
        server_index = original_cmd.index("--servers")
        if server_index + 1 < len(original_cmd):
            servers_str = original_cmd[server_index + 1]
            servers = servers_str.split(",")

    return {
        "success": False,
        "error": "Specified servers are not authenticated",
        "error_type": "servers_not_authenticated",
        "affected_servers": servers,
        "suggestion": "Run discovery on these servers first to establish authentication",
        "original_error": error_details,
        "recovery_action": "discovery_needed"
    }
