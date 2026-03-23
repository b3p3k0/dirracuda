"""
SMBSeek Scan Progress Helpers

Pure, stateless functions for scan progress computation.
Extracted from ScanManager to isolate progress transformation logic.

No imports from gui — only stdlib needed.
"""

from datetime import datetime
from typing import Optional


def detect_scan_phase(message: str) -> str:
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


def enhance_progress_message(
    message: str,
    percentage: float,
    phase: str,
    last_progress_update: Optional[dict] = None,
) -> str:
    """
    Enhance progress message with additional context and user feedback.

    Args:
        message: Original message from backend interface
        percentage: Current progress percentage
        phase: Detected scan phase
        last_progress_update: Dict from ScanManager.last_progress_update
                              (may contain "timestamp" key as ISO string).
                              Used to compute running-duration suffix when
                              percentage < 100 and elapsed > 60 s.
                              No phase-match gate — any prior timestamp triggers it.

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
    if last_progress_update:
        last_time = last_progress_update.get("timestamp")
        if last_time:
            time_diff = (datetime.now() - datetime.fromisoformat(last_time)).total_seconds()
            if time_diff > 60 and percentage < 100:  # More than 1 minute in same phase
                enhanced += f" (running {time_diff/60:.0f}m)"

    return enhanced
