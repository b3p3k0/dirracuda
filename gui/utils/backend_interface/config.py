"""
Configuration management for BackendInterface.

Contains boot-time configuration helpers for ensuring config files exist,
validating them, loading timeouts and workflow settings, and cleaning up
startup locks.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Dict, Any

from ..logging_config import get_logger

_logger = get_logger("backend_interface.config")


def ensure_config_exists(interface) -> None:
    """
    Ensure SMBSeek configuration file exists, creating from example if needed.

    Args:
        interface: BackendInterface instance

    Raises:
        RuntimeError: If configuration cannot be created or validated
    """
    if not interface.config_path.exists():
        if interface.config_example_path.exists():
            # Copy example config
            shutil.copy2(interface.config_example_path, interface.config_path)
        else:
            raise RuntimeError(f"Dirracuda configuration template not found at {interface.config_example_path}")


def validate_config(interface) -> Dict[str, Any]:
    """
    Validate SMBSeek configuration and return validation results.

    Args:
        interface: BackendInterface instance

    Returns:
        Dictionary with validation results and warnings
    """
    validation_result = {
        "valid": True,
        "warnings": [],
        "errors": []
    }

    if not interface.config_path.exists():
        validation_result["valid"] = False
        validation_result["errors"].append("Configuration file does not exist")
        return validation_result

    try:
        with open(interface.config_path, 'r') as f:
            config = json.load(f)

        # Check for required Shodan API key
        shodan_key = config.get('shodan', {}).get('api_key', '')
        if not shodan_key or shodan_key == 'YOUR_API_KEY_HERE':
            validation_result["warnings"].append(
                "Shodan API key not configured - discovery functionality will be limited"
            )

        # Validate country codes if specified
        if 'countries' in config:
            country_codes = config['countries']
            if not isinstance(country_codes, dict) or not country_codes:
                validation_result["warnings"].append("No country codes configured")

    except json.JSONDecodeError as e:
        validation_result["valid"] = False
        validation_result["errors"].append(f"Invalid JSON in configuration: {e}")
    except Exception as e:
        validation_result["valid"] = False
        validation_result["errors"].append(f"Error reading configuration: {e}")

    return validation_result


def load_timeout_configuration(interface) -> None:
    """
    Load timeout configuration from config files with environment override support.

    Args:
        interface: BackendInterface instance

    Configuration hierarchy (highest priority first):
    1. Environment variable: SMBSEEK_GUI_TIMEOUT
    2. conf/config.json gui_app.operation_timeout_seconds
    3. Default: None (no timeout)

    Design Decision: Multi-level configuration allows development, testing,
    and production flexibility while maintaining safe defaults.
    """
    try:
        # Check environment variable first (highest priority)
        env_timeout = os.environ.get('SMBSEEK_GUI_TIMEOUT')
        if env_timeout is not None:
            if env_timeout.lower() in ('none', 'null', '0'):
                interface.default_timeout = None
            else:
                interface.default_timeout = int(env_timeout)
            return

        if interface.config_path.exists():
            with open(interface.config_path, 'r') as f:
                config = json.load(f)

            gui_config = config.get('gui_app') or config.get('gui', {})
            interface.default_timeout = gui_config.get('operation_timeout_seconds', None)
            interface.enable_debug_timeouts = gui_config.get('enable_debug_timeouts', False)

            if interface.default_timeout == 0:
                interface.default_timeout = None

    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        # Fallback to safe default - no timeout
        interface.default_timeout = None
        interface.enable_debug_timeouts = False
        _logger.warning("Could not load timeout configuration: %s", e)
        _logger.warning("Using default: no timeout")


def load_workflow_configuration(interface) -> None:
    """
    Load workflow configuration from SMBSeek config file.

    Args:
        interface: BackendInterface instance

    Loads settings for recent filtering and other workflow parameters.
    """
    try:
        if interface.config_path.exists():
            with open(interface.config_path, 'r') as f:
                config = json.load(f)

            workflow_config = config.get('workflow', {})
            interface.default_recent_days = workflow_config.get('access_recent_days', 90)

    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        # Fallback to safe defaults
        interface.default_recent_days = 90
        _logger.warning("Could not load workflow configuration: %s", e)
        _logger.warning("Using default: 90 days for recent filtering")


def cleanup_startup_locks(interface) -> None:
    """
    Clean up stale lock files on startup as recommended by backend team.

    Args:
        interface: BackendInterface instance

    Ensures proper coordination between GUI and backend operations by
    removing orphaned lock files from crashed processes.
    """
    try:
        # Define GUI directory (parent of backend path for xsmbseek layout)
        gui_dir = interface.backend_path.parent

        # Clean up potential GUI lock files in xsmbseek directory
        gui_lock_patterns = [
            ".scan_lock",
            ".gui_operation_lock",
            ".access_verification_lock"
        ]

        for pattern in gui_lock_patterns:
            lock_path = gui_dir / pattern
            if lock_path.exists():
                try:
                    # Check if lock file contains process information
                    with open(lock_path, 'r') as f:
                        lock_data = json.load(f)

                    # Check if process is still running
                    pid = lock_data.get('process_id')
                    if pid and process_exists(pid):
                        # Process still exists, lock is valid - keep it
                        continue

                    # Process doesn't exist, remove stale lock
                    lock_path.unlink()

                except (json.JSONDecodeError, FileNotFoundError, KeyError):
                    # Invalid or corrupted lock file, remove it
                    if lock_path.exists():
                        lock_path.unlink()

    except Exception:
        # Non-critical cleanup failure - continue without error
        # Backend team noted this should be graceful and not interrupt operations
        pass


def process_exists(pid: int) -> bool:
    """
    Check if process with given PID exists (for lock file validation).

    Args:
        pid: Process ID to check

    Returns:
        True if process exists, False otherwise
    """
    try:
        # Try psutil first if available (more reliable)
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            pass

        # Fallback method using os.kill with signal 0
        import os
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def validate_backend(interface) -> None:
    """
    Validate that backend is accessible and functional.

    Raises:
        FileNotFoundError: If backend CLI script not found
        PermissionError: If backend not executable
    """
    if not interface.cli_script.exists():
        raise FileNotFoundError(
            f"Backend CLI not found at {interface.cli_script}. "
            f"Ensure backend is properly installed."
        )

    import os
    if not os.access(interface.cli_script, os.X_OK):
        raise PermissionError(
            f"Backend CLI not executable: {interface.cli_script}. "
            f"Run: chmod +x {interface.cli_script}"
        )
