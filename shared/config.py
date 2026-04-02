"""
Dirracuda Configuration Management

Centralized configuration loading and management for the unified CLI.
Handles the reorganized configuration structure while maintaining compatibility.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def load_json_config(config_file: str) -> Dict[str, Any]:
    """
    Backward-compatible helper for GUI/CLI components expecting a simple JSON loader.

    Returns an empty dict on failure to avoid crashing legacy import paths.
    """
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config {config_file}: {e}")
        return {}


def save_json_config(config_file: str, data: Dict[str, Any]) -> bool:
    """
    Backward-compatible helper to save JSON config safely.

    Returns True on success, False on failure.
    """
    try:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.warning(f"Failed to save config {config_file}: {e}")
        return False


def get_standard_timestamp() -> str:
    """
    Generate a UTC timestamp in canonical SQLite format YYYY-MM-DD HH:MM:SS.

    Uses UTC so writes are consistent with SQLite's CURRENT_TIMESTAMP default.

    Returns:
        Canonical DB timestamp string (YYYY-MM-DD HH:MM:SS, UTC, no T-separator)
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_db_timestamp(ts_str: str) -> str:
    """
    Convert any ISO-format timestamp string to canonical DB format
    (YYYY-MM-DD HH:MM:SS, UTC, no T-separator, no microseconds).

    Handles:
    - "2025-01-21 14:20:05"          -> unchanged
    - "2025-01-21T14:20:05"          -> "2025-01-21 14:20:05"  (assumed UTC)
    - "2025-01-21T14:20:05.123456"   -> "2025-01-21 14:20:05"  (truncated)
    - "2025-01-21T14:20:05Z"         -> "2025-01-21 14:20:05"
    - "2025-01-21T14:20:05+05:30"    -> "2025-01-21 08:50:05"  (converted to UTC)
    - None / non-string              -> returned unchanged

    Args:
        ts_str: Timestamp string to normalize (or None/non-string passthrough).

    Returns:
        Canonical DB timestamp string, or the original value if not a string.
    """
    if not isinstance(ts_str, str) or not ts_str.strip():
        return ts_str

    s = ts_str.strip().replace("T", " ")

    # Detect and apply UTC offset (e.g. +05:30 or -05:00)
    offset_match = re.search(r"([+-])(\d{2}):?(\d{2})$", s)
    if offset_match:
        sign_str, hh, mm = offset_match.groups()
        base = s[: offset_match.start()].rstrip()[:19]
        try:
            dt = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            # Fallback: just strip offset and truncate
            return base[:19]
        sign = 1 if sign_str == "+" else -1
        dt = dt - timedelta(hours=int(hh), minutes=int(mm)) * sign
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    # Z suffix (already UTC) or no offset (treated as UTC) — strip Z, truncate
    s = s.rstrip("Z").strip()
    return s[:19]


class SMBSeekConfig:
    """
    Centralized configuration management for Dirracuda.
    
    Loads and manages configuration from JSON files with sensible defaults
    and the new reorganized structure.
    """
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration manager.
        
        Args:
            config_file: Path to configuration file (default: conf/config.json)
        """
        if config_file is None:
            # Default configuration path from project root
            config_file = os.path.join("conf", "config.json")
        
        self.config_file = config_file
        self.config = self.load_configuration()
    
    def load_configuration(self) -> Dict[str, Any]:
        """
        Load configuration from JSON file with fallback to defaults.
        
        Returns:
            Configuration dictionary with all required sections
        """
        default_config = {
            "shodan": {
                "api_key": "",
                "query_limits": {
                    "max_results": 1000,
                    "timeout": 30
                },
                "query_components": {
                    "base_query": "smb authentication: disabled",
                    "product_filter": "product:\"Samba\"",
                    "additional_exclusions": ["-\"DSL\""],
                    "use_organization_exclusions": True,
                    "string_filters": [],
                    "string_combination": "OR"
                }
            },
            "workflow": {
                "rescan_after_days": 30,
                "pause_between_steps": False,
                "auto_collect_files": True,
                "skip_failed_hosts": True
            },
            "connection": {
                "timeout": 30,
                "port_check_timeout": 10,
                "rate_limit_delay": 3,
                "share_access_delay": 7
            },
            "discovery": {
                "max_concurrent_hosts": 10,
                "max_worker_cap": 20,
                "smart_throttling": False
            },
            "access": {
                "max_concurrent_hosts": 1
            },
            "file_collection": {
                "max_files_per_target": 3,
                "max_total_size_mb": 500,
                "download_delay_seconds": 2,
                "max_directory_depth": 3,
                "enumeration_timeout_seconds": 120,
                "included_extensions": [
                    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".csv",
                    ".eml", ".msg", ".mbox", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff",
                    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mp3", ".wav", ".zip", ".rar"
                ],
                "excluded_extensions": [
                    ".exe", ".dll", ".sys", ".bat", ".cmd", ".scr", ".com", ".pif", ".msi", 
                    ".bin", ".log", ".tmp", ".temp", ".bak", ".old", ".swp", ".lock"
                ]
            },
            "file_browser": {
                "allow_smb1": True,
                "connect_timeout_seconds": 8,
                "request_timeout_seconds": 10,
                "max_entries_per_dir": 5000,
                "max_depth": 12,
                "max_path_length": 240,
                "download_chunk_mb": 4,
                "quarantine_root": "~/.dirracuda/quarantine"
            },
            "quarantine": {
                "use_tmpfs": False,
                "tmpfs_size_mb": 512
            },
            "security": {
                "ransomware_indicators_path": "conf/ransomware_indicators.json",
                "exclusion_file": "conf/exclusion_list.json"
            },
            "database": {
                "path": "dirracuda.db",
                "backup_enabled": True,
                "backup_directory": "db_backups",
                "max_backup_files": 30
            },
            "output": {
                "colors_enabled": True,
                "verbose_by_default": False,
                "executive_summary": True
            }
        }
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
            
            # Merge user config with defaults (deep merge)
            merged_config = self._deep_merge(default_config, user_config)
            return merged_config
            
        except FileNotFoundError:
            print(f"⚠ Configuration file not found: {self.config_file}")
            print("⚠ Using default configuration values")
            return default_config
        except json.JSONDecodeError as e:
            print(f"⚠ Invalid JSON in configuration file: {e}")
            print("⚠ Using default configuration values")
            return default_config
        except Exception as e:
            print(f"⚠ Error loading configuration: {e}")
            print("⚠ Using default configuration values")
            return default_config
    
    def _deep_merge(self, default: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge user configuration with defaults.
        
        Args:
            default: Default configuration dictionary
            user: User configuration dictionary
            
        Returns:
            Merged configuration dictionary
        """
        result = default.copy()
        
        for key, value in user.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def get(self, section: str, key: Optional[str] = None, default: Any = None) -> Any:
        """
        Get configuration value.
        
        Args:
            section: Configuration section name
            key: Specific key within section (optional)
            default: Default value if not found
            
        Returns:
            Configuration value or default
        """
        if section not in self.config:
            return default
        
        if key is None:
            return self.config[section]
        
        return self.config[section].get(key, default)
    
    def get_shodan_api_key(self) -> str:
        """Get Shodan API key with validation."""
        api_key = self.get("shodan", "api_key", "")
        if not api_key:
            raise ValueError("Shodan API key not configured. Please set 'shodan.api_key' in config.json")
        return api_key
    
    def get_shodan_config(self) -> Dict[str, Any]:
        """Get Shodan configuration section."""
        return self.get("shodan", default={
            "api_key": "",
            "query_limits": {
                "max_results": 1000,
                "timeout": 30
            }
        })

    def get_ftp_config(self) -> Dict[str, Any]:
        """Get FTP configuration section with defaults."""
        return self.get("ftp") or {
            "shodan": {
                "query_components": {
                    "base_query": "port:21 \"230 Login successful\"",
                    "additional_exclusions": []
                },
                "query_limits": {"max_results": None}
            },
            "verification": {
                "connect_timeout": 5,
                "auth_timeout": 10,
                "listing_timeout": 15
            },
            "discovery": {"max_concurrent_hosts": 10},
            "access": {"max_concurrent_hosts": 4},
        }

    def get_max_concurrent_ftp_discovery_hosts(self) -> int:
        """Get max concurrent hosts for FTP discovery with validation. Default 10, min 1."""
        value = self.get_ftp_config().get("discovery", {}).get("max_concurrent_hosts", 10)
        if isinstance(value, int) and value >= 1:
            return value
        return 10

    def get_max_concurrent_ftp_access_hosts(self) -> int:
        """Get max concurrent hosts for FTP access with validation. Default 4, min 1."""
        value = self.get_ftp_config().get("access", {}).get("max_concurrent_hosts", 4)
        if isinstance(value, int) and value >= 1:
            return value
        return 4

    def get_http_config(self) -> Dict[str, Any]:
        """Get HTTP configuration section with defaults."""
        return self.get("http") or {
            "shodan": {
                "query_components": {
                    "base_query": 'http.title:"Index of /"',
                },
                "query_limits": {"max_results": None},
            },
            "verification": {
                "connect_timeout": 5,
                "request_timeout": 10,
                "subdir_timeout": 8,
                "allow_insecure_tls": True,
                "verify_http": True,
                "verify_https": True,
            },
            "discovery": {"max_concurrent_hosts": 10},
            "access": {"max_concurrent_hosts": 4},
        }

    def get_max_concurrent_http_discovery_hosts(self) -> int:
        """Get max concurrent hosts for HTTP discovery with validation. Default 10, min 1."""
        value = self.get_http_config().get("discovery", {}).get("max_concurrent_hosts", 10)
        return value if isinstance(value, int) and value >= 1 else 10

    def get_max_concurrent_http_access_hosts(self) -> int:
        """Get max concurrent hosts for HTTP access with validation. Default 4, min 1."""
        value = self.get_http_config().get("access", {}).get("max_concurrent_hosts", 4)
        return value if isinstance(value, int) and value >= 1 else 4

    def get_database_path(self) -> str:
        """Get database file path."""
        return self.get("database", "path", "dirracuda.db")
    
    def should_rescan_host(self, last_seen_days: int) -> bool:
        """
        Check if a host should be rescanned based on configuration.
        
        Args:
            last_seen_days: Days since host was last scanned
            
        Returns:
            True if host should be rescanned
        """
        rescan_after = self.get("workflow", "rescan_after_days", 30)
        return last_seen_days >= rescan_after
    
    def should_skip_failed_hosts(self) -> bool:
        """Check if failed hosts should be skipped by default."""
        return self.get("workflow", "skip_failed_hosts", True)
    
    def get_exclusion_file_path(self) -> str:
        """Get path to exclusion list file."""
        configured = self.get("security", "exclusion_file", "conf/exclusion_list.json")
        # Migrate legacy .txt configs to JSON path if encountered
        if configured.lower().endswith(".txt"):
            migrated = str(Path(configured).with_suffix(".json"))
            logger.warning(
                "Legacy exclusion_file path (%s) detected; switching to JSON file %s",
                configured,
                migrated,
            )
            return migrated
        return configured

    def get_exclusion_list(self) -> list:
        """
        Load exclusion organization list from JSON file.

        The exclusion list is now JSON-only. If the file is missing or invalid, an empty list is
        returned and a warning/error is logged.
        """
        path = Path(self.get_exclusion_file_path()).expanduser()
        exclusions: list = []

        def _normalize(seq):
            norm = []
            seen = set()
            for entry in seq:
                if not isinstance(entry, str):
                    continue
                cleaned = entry.strip()
                if not cleaned:
                    continue
                if cleaned in seen:
                    continue
                seen.add(cleaned)
                norm.append(cleaned)
            return norm

        if not path.exists():
            logger.warning("Exclusion file not found at %s; ensure conf/exclusion_list.json is present.", path)
            return exclusions

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("organizations", []) if isinstance(data, dict) else data
            exclusions = _normalize(raw if isinstance(raw, list) else [])
        except Exception:
            logger.error("Failed to parse exclusion list JSON at %s", path, exc_info=True)
            return []

        if not exclusions:
            logger.warning("Exclusion list is empty at %s; pull the latest from the repository.", path)

        return exclusions
    
    def get_ransomware_indicators(self) -> list:
        """
        Get ransomware indicator patterns.

        External file at security.ransomware_indicators_path (preferred).
        Returns an empty list if unavailable.
        """
        path = self.get("security", "ransomware_indicators_path", "")
        if path:
            from pathlib import Path
            try:
                data = Path(path).expanduser().read_text(encoding="utf-8")
                parsed = json.loads(data)
                if isinstance(parsed, list):
                    return [s for s in parsed if isinstance(s, str) and s.strip()]
            except FileNotFoundError:
                print("⚠ Ransomware patterns not found; pull the latest from the repo.")
            except Exception:
                print("⚠ Failed to load ransomware patterns file; falling back to defaults.")

        return []
    
    def get_connection_timeout(self) -> int:
        """Get SMB connection timeout."""
        return self.get("connection", "timeout", 30)
    
    def get_rate_limit_delay(self) -> int:
        """Get delay between connection attempts."""
        return self.get("connection", "rate_limit_delay", 3)
    
    def get_share_access_delay(self) -> int:
        """Get delay between share access tests on same host."""
        return self.get("connection", "share_access_delay", 7)

    def get_max_concurrent_hosts(self) -> int:
        """Get maximum concurrent hosts for access operations with validation."""
        value = self.get("access", "max_concurrent_hosts", 1)
        if isinstance(value, int) and value >= 1:
            return value
        else:
            # Fallback to 1 for invalid values (zero, negative, or non-integer)
            return 1

    def get_max_concurrent_discovery_hosts(self) -> int:
        """Get maximum concurrent hosts for discovery operations with validation."""
        value = self.get("discovery", "max_concurrent_hosts", 10)
        if isinstance(value, int) and value >= 1:
            return value
        else:
            # Fallback to 10 for invalid values (zero, negative, or non-integer)
            return 10

    def get_discovery_smart_throttling(self) -> bool:
        """Get whether smart throttling is enabled for discovery."""
        return self.get("discovery", "smart_throttling", False)

    def get_max_worker_cap(self) -> int:
        """Get maximum worker cap for thread pool safety with validation."""
        value = self.get("discovery", "max_worker_cap", 20)
        if isinstance(value, int) and value >= 1:
            return value
        else:
            # Fallback to 20 for invalid values (zero, negative, or non-integer)
            return 20

    def resolve_target_countries(self, args_country: Optional[str] = None) -> list:
        """
        Resolve target countries using explicit argument or global scan.

        Args:
            args_country: Country argument from command line (optional)

        Returns:
            List of country codes to scan, empty list for global scan
        """
        # Use --country flag if provided
        if args_country:
            # Handle comma-separated countries
            return [country.strip().upper() for country in args_country.split(',')]

        # Default to global scan (no country filter)
        return []
    
    def validate_configuration(self) -> bool:
        """
        Validate configuration for common issues.
        
        Returns:
            True if configuration appears valid
        """
        issues = []
        
        # Check Shodan API key
        if not self.get("shodan", "api_key"):
            issues.append("Missing Shodan API key")
        
        # Check exclusion file exists
        exclusion_file = self.get_exclusion_file_path()
        if not os.path.exists(exclusion_file):
            issues.append(f"Exclusion file not found: {exclusion_file}")
        
        # Check reasonable values
        if self.get("workflow", "rescan_after_days", 30) < 1:
            issues.append("rescan_after_days must be at least 1")
        
        if self.get("connection", "timeout", 30) < 5:
            issues.append("connection timeout should be at least 5 seconds")
        
        if issues:
            print("⚠ Configuration validation issues:")
            for issue in issues:
                print(f"  • {issue}")
            return False

        return True

    # RCE Module Configuration Methods

    def get_rce_config(self) -> Dict[str, Any]:
        """
        Get RCE module configuration with defaults merged.

        Returns:
            Full RCE configuration section with safe defaults applied.
        """
        defaults = {
            "enabled_default": False,
            "safe_active_budget": {
                "max_requests": 2,
                "per_host_timeout_seconds": 5,
                "retry_count": 0,
                "jitter_ms": 250
            },
            "ms17_010": {"enabled": True},
            "smbghost": {"enabled": True},
            "logging": {"jsonl_path": "~/.dirracuda/logs/rce_analysis.jsonl"},
            "intrusive_mode_enabled": False
        }
        user_rce = self.config.get("rce", {})
        return self._deep_merge(defaults, user_rce)

    def get_rce_safe_budget(self) -> Dict[str, Any]:
        """
        Get safe-active probe budget configuration.

        Returns:
            Budget configuration dict with max_requests, timeout, retry, jitter.
        """
        return self.get_rce_config().get("safe_active_budget", {
            "max_requests": 2,
            "per_host_timeout_seconds": 5,
            "retry_count": 0,
            "jitter_ms": 250
        })

    def is_rce_enabled_by_default(self) -> bool:
        """Check if RCE analysis is enabled by default."""
        return self.get_rce_config().get("enabled_default", False)

    def is_intrusive_mode_enabled(self) -> bool:
        """
        Check if intrusive RCE probes are enabled.

        This is a safety guard - intrusive mode should always be False
        unless explicitly enabled in config for authorized testing.
        """
        return self.get_rce_config().get("intrusive_mode_enabled", False)

    def get_rce_logging_path(self) -> str:
        """Get JSONL logging path for RCE analysis results."""
        return self.get_rce_config().get("logging", {}).get(
            "jsonl_path", "~/.dirracuda/logs/rce_analysis.jsonl"
        )

    def is_ms17_010_enabled(self) -> bool:
        """Check if MS17-010 safe probe is enabled."""
        return self.get_rce_config().get("ms17_010", {}).get("enabled", True)

    def is_smbghost_enabled(self) -> bool:
        """Check if SMBGhost exposure check is enabled."""
        return self.get_rce_config().get("smbghost", {}).get("enabled", True)

    def get_clamav_config(self) -> Dict[str, Any]:
        """
        Get ClamAV configuration with defaults merged.

        Returns:
            Full ClamAV configuration section with safe defaults applied.
        """
        defaults = {
            "enabled": False,
            "backend": "auto",
            "timeout_seconds": 60,
            "extracted_root": "~/.dirracuda/extracted",
            "known_bad_subdir": "known_bad",
            "show_results": True,
            "auto_promote_clean_files": False,
        }
        user_clamav = self.config.get("clamav", {})
        if not isinstance(user_clamav, dict):
            return defaults
        return self._deep_merge(defaults, user_clamav)

    def get_quarantine_config(self) -> Dict[str, Any]:
        """Get quarantine runtime config with tmpfs defaults merged."""
        defaults = {
            "use_tmpfs": False,
            "tmpfs_size_mb": 512,
        }
        user_quarantine = self.config.get("quarantine", {})
        if not isinstance(user_quarantine, dict):
            return defaults
        merged = self._deep_merge(defaults, user_quarantine)
        try:
            merged["tmpfs_size_mb"] = int(merged.get("tmpfs_size_mb", 512))
        except (TypeError, ValueError):
            merged["tmpfs_size_mb"] = 512
        return merged


def load_config(config_file: Optional[str] = None) -> SMBSeekConfig:
    """
    Convenience function to load Dirracuda configuration.
    
    Args:
        config_file: Path to configuration file
        
    Returns:
        SMBSeekConfig instance
    """
    return SMBSeekConfig(config_file)
