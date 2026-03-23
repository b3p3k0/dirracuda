"""
Parity tests for scan_progress pure functions.

Covers:
- detect_scan_phase(): all phase keywords and fallback
- enhance_progress_message(): spinner formula, running-duration suffix, no-prior-update case
"""

from datetime import datetime, timedelta
import pytest

from gui.utils.scan_progress import detect_scan_phase, enhance_progress_message


class TestDetectScanPhase:
    def test_completed_keyword(self):
        assert detect_scan_phase("Scan complete") == "completed"
        assert detect_scan_phase("FINISHED scanning") == "completed"
        assert detect_scan_phase("Done.") == "completed"

    def test_access_testing_auth_keywords(self):
        assert detect_scan_phase("Testing auth on host") == "access_testing"
        assert detect_scan_phase("Enumerating shares") == "access_testing"
        assert detect_scan_phase("Access check in progress") == "access_testing"

    def test_access_testing_scoreboard(self):
        assert detect_scan_phase("Testing hosts: success: 3 failed: 1") == "access_testing"
        assert detect_scan_phase("Auth results available") == "access_testing"

    def test_discovery_keywords(self):
        assert detect_scan_phase("Querying Shodan API") == "discovery"
        assert detect_scan_phase("Discovering hosts") == "discovery"
        assert detect_scan_phase("Search in progress") == "discovery"

    def test_initialization_keywords(self):
        assert detect_scan_phase("Initializing scan") == "initialization"
        assert detect_scan_phase("Starting up") == "initialization"
        assert detect_scan_phase("Begin scan sequence") == "initialization"

    def test_error_indicators(self):
        assert detect_scan_phase("Scan failed: timeout") == "error"
        assert detect_scan_phase("Critical error encountered") == "error"
        assert detect_scan_phase("Fatal exception") == "error"

    def test_fallback_scanning(self):
        assert detect_scan_phase("Processing batch 3 of 10") == "scanning"
        assert detect_scan_phase("") == "scanning"
        assert detect_scan_phase("xyz unknown message") == "scanning"


class TestEnhanceProgressMessage:
    def test_spinner_formula_percentage_based(self):
        """Spinner index = int((percentage // 2) % 10); same input → same char."""
        activity_indicators = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

        # percentage=0.0 → index int((0//2) % 10) = 0 → '⠋'
        result0 = enhance_progress_message("msg", 0.0, "scanning")
        assert result0.startswith(activity_indicators[0])

        # percentage=22.0 → index int((22//2) % 10) = int(11 % 10) = 1 → '⠙'
        result22 = enhance_progress_message("msg", 22.0, "scanning")
        assert result22.startswith(activity_indicators[1])

        # percentage=40.0 → index int((40//2) % 10) = int(20 % 10) = 0 → '⠋'
        result40 = enhance_progress_message("msg", 40.0, "scanning")
        assert result40.startswith(activity_indicators[0])

    def test_spinner_same_call_twice_returns_same_char(self):
        """Spinner must not vary with wall-clock time."""
        r1 = enhance_progress_message("msg", 50.0, "scanning")
        r2 = enhance_progress_message("msg", 50.0, "scanning")
        assert r1 == r2

    def test_includes_running_duration_when_timestamp_old(self):
        """When last_progress_update has a timestamp >60s ago and pct<100, output contains 'running'."""
        old_ts = (datetime.now() - timedelta(seconds=90)).isoformat()
        result = enhance_progress_message(
            "Working...", 50.0, "scanning",
            last_progress_update={"timestamp": old_ts}
        )
        assert "running" in result

    def test_no_running_duration_when_timestamp_recent(self):
        """When timestamp is <60s ago, no running suffix."""
        recent_ts = datetime.now().isoformat()
        result = enhance_progress_message(
            "Working...", 50.0, "scanning",
            last_progress_update={"timestamp": recent_ts}
        )
        assert "running" not in result

    def test_no_running_duration_when_no_prior_update(self):
        """last_progress_update=None → no running suffix."""
        result = enhance_progress_message("msg", 50.0, "scanning", last_progress_update=None)
        assert "running" not in result

    def test_completed_phase_uses_simple_format(self):
        """Completed phase: no spinner, no percentage, just prefix + message."""
        result = enhance_progress_message("All done", 100.0, "completed")
        assert "✅" in result
        assert "⠋" not in result  # no spinner char in result
        assert "100%" not in result

    def test_active_scan_includes_percentage(self):
        result = enhance_progress_message("Scanning host", 42.0, "scanning")
        assert "42%" in result
