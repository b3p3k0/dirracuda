"""Regression tests for parse_final_results() against FTP-style CLI output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.backend_interface.progress import parse_final_results

_BLUE  = "\033[94m"
_RESET = "\033[0m"

# Only parser-critical lines — workflow steps, rollup stats, success marker.
_FTP_OUTPUT_WITH_ANSI = (
    f"{_BLUE}[1/2] FTP Discovery{_RESET}\n"
    f"{_BLUE}[2/2] FTP Access Verification{_RESET}\n"
    "📊 Hosts Scanned: 42\n"
    "🔓 Hosts Accessible: 7\n"
    "📁 Accessible Directories: 13\n"
    "🎉 FTP scan completed successfully\n"
)

_FTP_OUTPUT_NO_ANSI = (
    "[1/2] FTP Discovery\n"
    "[2/2] FTP Access Verification\n"
    "📊 Hosts Scanned: 10\n"
    "🔓 Hosts Accessible: 3\n"
    "📁 Accessible Shares: 2\n"
    "🎉 FTP scan completed successfully\n"
)


class TestFtpProgressParsing:
    def test_parse_rollup_with_ansi(self):
        """ANSI-wrapped output is stripped and rollup stats parse correctly."""
        result = parse_final_results(_FTP_OUTPUT_WITH_ANSI)
        assert result["hosts_scanned"] == 42
        assert result["hosts_accessible"] == 7
        assert result["accessible_shares"] == 13

    def test_success_marker_detected(self):
        """🎉 FTP scan completed successfully sets success=True."""
        result = parse_final_results(_FTP_OUTPUT_WITH_ANSI)
        assert result["success"] is True

    def test_parse_rollup_no_ansi(self):
        """Legacy 'Accessible Shares' output still parses for compatibility."""
        result = parse_final_results(_FTP_OUTPUT_NO_ANSI)
        assert result["hosts_scanned"] == 10
        assert result["hosts_accessible"] == 3
        assert result["accessible_shares"] == 2
        assert result["success"] is True
