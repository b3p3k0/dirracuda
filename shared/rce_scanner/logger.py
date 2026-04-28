"""
RCE JSONL Logger

Logs RCE analysis results in JSONL format for audit trails and analysis.
Each line is a self-contained JSON record with full evidence.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from shared.path_service import get_paths, get_legacy_paths, select_existing_path

logger = logging.getLogger(__name__)
_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)
_DEFAULT_LOG_PATH = select_existing_path(
    _PATHS.rce_analysis_log_file,
    [
        _LEGACY.flat_rce_analysis_log_file,
        _LEGACY.legacy_home_root / "logs" / "rce_analysis.jsonl",
        _LEGACY.legacy_home_root / "rce_analysis.jsonl",
    ],
)


class RceJsonlLogger:
    """
    JSONL logger for RCE analysis results.

    Writes one JSON record per line per host analysis, enabling
    easy parsing and streaming analysis of results.
    """

    def __init__(self, log_path: Optional[str] = None):
        """
        Initialize JSONL logger.

        Args:
            log_path: Path to JSONL file. If None, uses default path.
                      Supports ~ expansion for home directory.
        """
        if log_path is None:
            log_path = str(_DEFAULT_LOG_PATH)

        self.path = Path(log_path).expanduser()
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure parent directory exists."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            logger.warning(f"Could not create JSONL log directory: {e}")

    def log_host_analysis(self, ip: str, report: Dict[str, Any],
                          session_id: Optional[int] = None) -> bool:
        """
        Append one JSONL record for a host analysis.

        Args:
            ip: IP address of analyzed host
            report: Full RCE analysis report from reporter
            session_id: Optional scan session ID for correlation

        Returns:
            True if log was written successfully
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ip": ip,
            "verdict": report.get("verdict"),
            "rce_status": report.get("rce_status"),
            "not_assessable_reason": report.get("not_assessable_reason"),
            "score": report.get("score"),
            "findings": report.get("findings", []),
            "evidence": report.get("evidence", []),
            "matched_rules_count": len(report.get("matched_rules", [])),
        }

        # Add optional session correlation
        if session_id is not None:
            record["session_id"] = session_id

        # Add key facts if available
        metadata = report.get("analysis_metadata", {})
        if metadata:
            facts = metadata.get("facts_analyzed", {})
            if facts:
                record["key_facts"] = {
                    "smb1_possible": facts.get("smb1_possible"),
                    "has_compression": facts.get("has_compression"),
                    "anonymous_access": facts.get("anonymous_access"),
                    "has_domain_role_indicators": facts.get("has_domain_role_indicators"),
                    "shodan_os": facts.get("shodan_os"),
                }

        return self._write_record(record)

    def log_probe_error(self, ip: str, error_type: str,
                        error_message: str,
                        session_id: Optional[int] = None) -> bool:
        """
        Log a probe error for a host.

        Args:
            ip: IP address of host
            error_type: Type of error (timeout, connection_refused, etc.)
            error_message: Detailed error message
            session_id: Optional scan session ID

        Returns:
            True if log was written successfully
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ip": ip,
            "verdict": "error",
            "rce_status": "not_run",
            "error_type": error_type,
            "error_message": error_message,
        }

        if session_id is not None:
            record["session_id"] = session_id

        return self._write_record(record)

    def log_batch_summary(self, session_id: int,
                          total_hosts: int,
                          results_summary: Dict[str, int]) -> bool:
        """
        Log a summary record for a batch/session.

        Args:
            session_id: Scan session ID
            total_hosts: Total hosts analyzed
            results_summary: Dict with counts by verdict type

        Returns:
            True if log was written successfully
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "record_type": "batch_summary",
            "session_id": session_id,
            "total_hosts": total_hosts,
            "by_verdict": results_summary,
        }

        return self._write_record(record)

    def _write_record(self, record: Dict[str, Any]) -> bool:
        """
        Write a single JSONL record.

        Args:
            record: Dict to serialize as JSON

        Returns:
            True if write succeeded
        """
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            return True
        except (OSError, PermissionError, IOError) as e:
            logger.error(f"Failed to write JSONL record: {e}")
            return False

    def get_log_path(self) -> str:
        """Get the current log file path."""
        return str(self.path)

    def rotate_if_needed(self, max_size_mb: float = 100.0) -> bool:
        """
        Rotate log file if it exceeds max size.

        Args:
            max_size_mb: Maximum file size in megabytes

        Returns:
            True if rotation was performed
        """
        try:
            if not self.path.exists():
                return False

            size_mb = self.path.stat().st_size / (1024 * 1024)
            if size_mb < max_size_mb:
                return False

            # Create rotated filename with timestamp
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rotated_path = self.path.with_suffix(f".{timestamp}.jsonl")

            self.path.rename(rotated_path)
            logger.info(f"Rotated RCE log to {rotated_path}")
            return True

        except (OSError, PermissionError) as e:
            logger.warning(f"Failed to rotate RCE log: {e}")
            return False


def create_logger_from_config(config: Any) -> RceJsonlLogger:
    """
    Create JSONL logger from SMBSeekConfig.

    Args:
        config: SMBSeekConfig instance

    Returns:
        Configured RceJsonlLogger instance
    """
    log_path = None
    if hasattr(config, 'get_rce_logging_path'):
        log_path = config.get_rce_logging_path()

    return RceJsonlLogger(log_path)


__all__ = ["RceJsonlLogger", "create_logger_from_config"]
