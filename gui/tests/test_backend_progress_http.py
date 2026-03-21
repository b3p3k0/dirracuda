"""Regression tests for parse_final_results() against HTTP-style CLI output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from gui.utils.backend_interface.progress import parse_final_results

_BLUE  = "\033[94m"
_RESET = "\033[0m"

# Only parser-critical lines — workflow steps, rollup stats, success marker.
_HTTP_OUTPUT_WITH_ANSI = (
    f"{_BLUE}[1/2] HTTP Discovery{_RESET}\n"
    f"{_BLUE}[2/2] HTTP Access Verification{_RESET}\n"
    "📊 Hosts Scanned: 15\n"
    "🔓 Hosts Accessible: 4\n"
    "📁 Accessible Directories: 9\n"
    "🎉 HTTP scan completed successfully\n"
)


class TestHttpProgressParsing:
    def test_parse_rollup_with_ansi(self):
        """ANSI-wrapped HTTP output is stripped and rollup stats parse correctly."""
        result = parse_final_results(_HTTP_OUTPUT_WITH_ANSI)
        assert result["hosts_scanned"] == 15
        assert result["hosts_accessible"] == 4
        assert result["accessible_shares"] == 9

    def test_success_marker_detected(self):
        """🎉 HTTP scan completed successfully sets success=True."""
        result = parse_final_results(_HTTP_OUTPUT_WITH_ANSI)
        assert result["success"] is True
