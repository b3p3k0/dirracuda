"""Tests for C11C semantic live output coloring (log_semantic_color satellite)."""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components import dashboard_logs, log_semantic_color
from gui.components.log_semantic_color import (
    SUMMARY_TITLE,
    classify_rollup_line,
    classify_status_message,
    colorize_for_display,
    wrap,
    _classify_sync_line,
)


# ---------------------------------------------------------------------------
# Minimal mock infrastructure
# ---------------------------------------------------------------------------

class _MockText:
    """Records (text, tags) pairs passed to insert(); stubs configure/delete/see."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, tuple]] = []

    def insert(self, index, text, tags=()):
        self.inserts.append((text, tags if isinstance(tags, tuple) else tuple(tags)))

    def configure(self, **_kw):
        pass

    def delete(self, *_args):
        pass

    def see(self, *_args):
        pass

    def yview(self):
        return (0.0, 1.0)

    def tag_configure(self, *_args, **_kw):
        pass


class _MockParent:
    def clipboard_clear(self):
        pass

    def clipboard_append(self, text):
        self._clipboard = text

    def after(self, *_args):
        pass

    def winfo_exists(self):
        return True


def _mock_dash(text_widget=None):
    """Build a minimal dash stub compatible with dashboard_logs functions."""

    class _Dash:
        pass

    d = _Dash()
    d.log_text_widget = text_widget or _MockText()
    d.log_history = deque(maxlen=500)
    d._log_placeholder_visible = False
    d.log_autoscroll = True
    d.log_jump_button = None
    d._ansi_pattern = re.compile(r"\x1b\[([\d;]*)m")
    d._ansi_color_tag_map = {
        "30": "ansi_fg_black", "31": "ansi_fg_red", "32": "ansi_fg_green",
        "33": "ansi_fg_yellow", "34": "ansi_fg_blue", "35": "ansi_fg_magenta",
        "36": "ansi_fg_cyan", "37": "ansi_fg_white",
        "90": "ansi_fg_bright_black", "91": "ansi_fg_bright_red",
        "92": "ansi_fg_bright_green", "93": "ansi_fg_bright_yellow",
        "94": "ansi_fg_bright_blue", "95": "ansi_fg_bright_magenta",
        "96": "ansi_fg_bright_cyan", "97": "ansi_fg_bright_white",
    }
    d._ansi_color_tags = set(d._ansi_color_tag_map.values())
    d._parse_ansi_segments = lambda text: dashboard_logs.parse_ansi_segments(d, text)
    d._apply_ansi_codes = lambda tags, codes: dashboard_logs.apply_ansi_codes(d, tags, codes)
    d._update_log_autoscroll_state = lambda *_: None
    d._show_log_jump_button = lambda: None
    d._hide_log_jump_button = lambda: None
    d.parent = _MockParent()
    return d


# ---------------------------------------------------------------------------
# 1. ANSI code and reset boundary
# ---------------------------------------------------------------------------

def test_wrap_blue():
    assert wrap("hello", "blue") == "\x1b[94mhello\x1b[0m"

def test_wrap_green():
    assert wrap("hello", "green") == "\x1b[92mhello\x1b[0m"

def test_wrap_yellow():
    assert wrap("hello", "yellow") == "\x1b[93mhello\x1b[0m"

def test_wrap_red():
    assert wrap("hello", "red") == "\x1b[91mhello\x1b[0m"

def test_wrap_empty_level_returns_text_unchanged():
    assert wrap("hello", "") == "hello"

def test_wrap_unknown_level_returns_text_unchanged():
    assert wrap("hello", "purple") == "hello"

def test_each_wrap_ends_with_reset():
    for level in ("blue", "green", "yellow", "red"):
        assert wrap("x", level).endswith("\x1b[0m"), f"level={level} missing reset"

def test_wrap_empty_text_returns_empty():
    assert wrap("", "blue") == ""


# ---------------------------------------------------------------------------
# 2. One-argument _log_status_event validity
# ---------------------------------------------------------------------------

def test_log_status_event_one_arg_is_valid():
    import queue as _queue
    from gui.components.dashboard import DashboardWidget
    dash = DashboardWidget.__new__(DashboardWidget)
    dash.log_queue = _queue.Queue()
    dash._log_status_event("test message")
    assert not dash.log_queue.empty()


# ---------------------------------------------------------------------------
# 3. SearXNG service message classification
# ---------------------------------------------------------------------------

def test_classify_reachability_checking_is_blue():
    assert classify_status_message("Reachability: checking https://searx.example.com...") == "blue"

def test_classify_run_registered_starting_fetch_is_blue():
    assert classify_status_message("Run registered (#42). Starting fetch...") == "blue"

def test_classify_querying_searxng_page_is_blue():
    assert classify_status_message("Querying SearXNG page 3...") == "blue"

def test_classify_querying_searxng_page_retry_is_blue():
    assert classify_status_message("Querying SearXNG page 3 (retry)...") == "blue"

def test_classify_instance_reachable_is_green():
    assert classify_status_message("Instance reachable") == "green"

def test_classify_run_complete_is_green():
    assert classify_status_message(
        "Run complete: fetched 42, verified 38, retained 12 open indexes."
    ) == "green"

def test_classify_page_classified_retained_is_green():
    assert classify_status_message(
        "Page 2: classified 8; retained 3 open indexes."
    ) == "green"

def test_classify_page_probed_is_green():
    assert classify_status_message(
        "Page 2: probed 3 (2 clean, 1 flagged, 0 unprobed)."
    ) == "green"

def test_classify_upstream_availability_recovered_is_green():
    assert classify_status_message("Upstream availability recovered after 1 retry.") == "green"

def test_classify_upstream_retry_is_yellow():
    assert classify_status_message("Upstream retry 1/2: waiting 30s...") == "yellow"

def test_classify_upstream_retry_remaining_is_yellow():
    assert classify_status_message("Upstream retry 1/2: 25s remaining...") == "yellow"

def test_classify_upstream_throttling_persisted_is_yellow():
    assert classify_status_message(
        "Upstream throttling persisted after 2 retries; pagination stopped early."
    ) == "yellow"

def test_classify_temporary_upstream_failures_is_yellow():
    assert classify_status_message(
        "Temporary upstream engine failures affected 2 pages; continued with soft backoff."
    ) == "yellow"

def test_classify_page_engine_warnings_is_yellow():
    assert classify_status_message(
        "Page 3: 2 upstream engine warning(s); continuing with soft backoff."
    ) == "yellow"

def test_classify_searxng_pagination_stopped_is_yellow():
    assert classify_status_message(
        "SearXNG pagination stopped after page 2: HTTP 503"
    ) == "yellow"

def test_classify_probe_indicator_warning_is_yellow():
    assert classify_status_message(
        "Probe indicator setup warning: config not found"
    ) == "yellow"

def test_classify_probe_results_not_saved_is_yellow():
    assert classify_status_message(
        "Page 2: probe results could not be saved: disk full"
    ) == "yellow"

def test_classify_cancelled_exact_is_yellow():
    assert classify_status_message("Cancelled.") == "yellow"

def test_classify_reachability_error_is_red():
    assert classify_status_message("Reachability error: connection refused") == "red"

def test_classify_reachability_failed_is_red():
    assert classify_status_message("Reachability failed: 503 Service Unavailable") == "red"

def test_classify_database_error_is_red():
    assert classify_status_message("Database error: table missing") == "red"

def test_classify_run_setup_failed_is_red():
    assert classify_status_message("Run setup failed: permission denied") == "red"

def test_classify_processing_error_is_red():
    assert classify_status_message(
        "Processing error: Page 3 processing failed: timeout"
    ) == "red"

def test_classify_fetch_error_is_red():
    assert classify_status_message("Fetch error: connection reset") == "red"

def test_classify_cancellation_finalization_failed_is_red():
    assert classify_status_message(
        "Cancellation finalization failed: DB locked"
    ) == "red"

# Normal messages — no color
def test_classify_page_received_results_is_normal():
    assert classify_status_message(
        "Page 2: received 10 results, 8 new (28 unique total)."
    ) == ""

def test_classify_page_stored_rows_is_normal():
    assert classify_status_message("Page 2: stored 10 rows.") == ""

def test_classify_page_classifying_progress_is_normal():
    assert classify_status_message("Page 2: classifying 5/10...") == ""

def test_classify_page_probing_progress_is_normal():
    assert classify_status_message("Page 2: probing 3/10...") == ""

def test_classify_opening_database_is_normal():
    assert classify_status_message("Opening database...") == ""

def test_classify_page_processing_covered_pacing_is_normal():
    assert classify_status_message(
        "Page 2: processing covered the 10.0s pacing window; continuing."
    ) == ""

def test_classify_page_processing_complete_waiting_is_normal():
    assert classify_status_message(
        "Page 2: processing complete; waiting 8.3s before page 3."
    ) == ""


# ---------------------------------------------------------------------------
# 4. SearXNG dashboard-level messages
# ---------------------------------------------------------------------------

def test_classify_searxng_search_started_is_blue():
    assert classify_status_message(
        'SearXNG search started: https://s.example.com | query: "open directory"'
    ) == "blue"

def test_classify_searxng_search_cancelled_is_yellow():
    assert classify_status_message("SearXNG search cancelled.") == "yellow"

def test_classify_searxng_search_failed_is_red():
    assert classify_status_message("SearXNG search failed: timeout") == "red"


# ---------------------------------------------------------------------------
# 5. Reddit unified and Grab messages
# ---------------------------------------------------------------------------

def test_classify_reddit_feed_ingest_started_is_blue():
    assert classify_status_message(
        "Reddit feed ingest started (sort: new, max_posts: 100)"
    ) == "blue"

def test_classify_reddit_search_ingest_started_is_blue():
    assert classify_status_message(
        "Reddit search ingest started (sort: top, max_posts: 50)"
    ) == "blue"

def test_classify_fetching_reddit_posts_is_blue():
    assert classify_status_message("Fetching Reddit posts...") == "blue"

def test_classify_reddit_ingest_failed_is_red():
    assert classify_status_message("Reddit ingest failed: timeout") == "red"

def test_classify_reddit_grab_started_is_blue():
    assert classify_status_message(
        "Reddit Grab started (sort=new, max_posts=100)"
    ) == "blue"

def test_classify_reddit_grab_failed_is_red():
    assert classify_status_message("Reddit Grab failed: HTTP 429") == "red"


# ---------------------------------------------------------------------------
# 6. Provider queue messages
# ---------------------------------------------------------------------------

def test_classify_provider_queue_overview_is_blue():
    assert classify_status_message(
        "Provider queue: Reddit -> SearXNG -> Shodan"
    ) == "blue"

def test_classify_provider_queue_starting_is_blue():
    assert classify_status_message("Provider queue starting: SearXNG") == "blue"

def test_classify_provider_queue_completed_is_green():
    assert classify_status_message("Provider queue completed: Reddit") == "green"

def test_classify_provider_queue_failed_is_red():
    assert classify_status_message(
        "Provider queue failed: SearXNG: timeout"
    ) == "red"

def test_classify_provider_queue_cancelled_is_yellow():
    assert classify_status_message("Unified provider queue cancelled.") == "yellow"

def test_classify_provider_queue_finished_all_success_is_green():
    assert classify_status_message(
        "Provider queue finished: 3/3 providers completed."
    ) == "green"

def test_classify_provider_queue_finished_partial_is_yellow():
    assert classify_status_message(
        "Provider queue finished: 3/3 providers attempted (2 failed)."
    ) == "yellow"

def test_classify_provider_queue_finished_zero_failed_is_green():
    # "(0 failed)" must not trigger the yellow branch — count must be >= 1
    assert classify_status_message(
        "Provider queue finished: 3/3 providers attempted (0 failed)."
    ) == "green"


# ---------------------------------------------------------------------------
# 7. Shodan does not collide
# ---------------------------------------------------------------------------

def test_shodan_status_line_not_matched():
    assert classify_status_message("SMB scan started: US, 42 hosts") == ""

def test_shodan_backend_line_unchanged():
    line = "\x1b[32mShodan result\x1b[0m"
    assert colorize_for_display(line) == line

def test_shodan_heading_standalone_not_colorized():
    # Standalone SUMMARY_TITLE (no \n) — Shodan with CLI colors disabled
    assert colorize_for_display(SUMMARY_TITLE) == SUMMARY_TITLE


# ---------------------------------------------------------------------------
# 8. Rollup per-line coloring
# ---------------------------------------------------------------------------

def test_rollup_title_is_blue():
    assert classify_rollup_line(SUMMARY_TITLE) == "blue"

def test_rollup_divider_is_blue():
    assert classify_rollup_line("=" * len(SUMMARY_TITLE)) == "blue"

def test_rollup_source_line_is_blue():
    assert classify_rollup_line("🌍 SearXNG Query: open directory") == "blue"

def test_rollup_metric_line_is_normal():
    assert classify_rollup_line("📊 URLs Fetched: 42") == ""

def test_rollup_warning_line_is_yellow():
    assert classify_rollup_line(
        "⚠ Upstream Engine Warnings: 1 engines across 2 pages; continued with soft backoff"
    ) == "yellow"

def test_rollup_retry_line_is_yellow():
    assert classify_rollup_line(
        "⏳ Upstream Retry: 30s across 1 retries; recovered"
    ) == "yellow"

def test_rollup_cancelled_terminal_is_yellow():
    assert classify_rollup_line("⚠ Scan cancelled: 3 open indexes retained") == "yellow"

def test_rollup_success_terminal_is_green():
    assert classify_rollup_line("✓ Scan completed: Found 12 open indexes") == "green"

def test_rollup_informational_terminal_is_normal():
    assert classify_rollup_line("ℹ Scan completed: No open indexes found") == ""

def test_rollup_sync_success_is_green():
    line = "🔄 Primary DB Sync: 10 processed (8 inserted, 2 updated, 0 skipped, 0 failed)"
    assert classify_rollup_line(line) == "green"

def test_rollup_sync_failed_is_red():
    line = "🔄 Primary DB Sync: 10 processed (8 inserted, 0 updated, 0 skipped, 2 failed)"
    assert classify_rollup_line(line) == "red"

def test_rollup_sync_cancelled_is_yellow():
    line = "🔄 Primary DB Sync: 5 processed (4 inserted, 0 updated, 1 skipped, 0 failed, 1 cancelled)"
    assert classify_rollup_line(line) == "yellow"

def test_rollup_sync_unknown_format_is_normal():
    # Sync line with no "N failed" token → unknown format → normal
    assert classify_rollup_line("🔄 Primary DB Sync: unknown format") == ""

def test_failed_sync_not_green():
    line = "🔄 Primary DB Sync: 5 processed (3 inserted, 0 updated, 0 skipped, 2 failed)"
    assert _classify_sync_line(line) == "red"
    assert _classify_sync_line(line) != "green"

def test_rollup_multiline_each_colored_line_has_own_reset():
    from gui.components.dashboard_scan_rollup import format_searxng_rollup

    class _FakeResult:
        fetched_count = 10
        verified_count = 8
        deduped_count = 3
        pages_fetched = 2
        pacing_delay_seconds = 5.0
        throttled_page_count = 0
        throttle_engines = ()
        hard_retry_count = 0
        hard_retry_delay_seconds = 0.0
        stopped_early = False
        probe_enabled = False
        fetch_warning = ""

    rollup = format_searxng_rollup(_FakeResult(), query="test", db_path="/tmp/db.db")
    colored = log_semantic_color._color_rollup_block(rollup)
    parts = colored.split("\n")
    for part in parts:
        if "\x1b[" in part:
            assert part.endswith("\x1b[0m"), (
                f"Colored rollup line missing reset: {part!r}"
            )


# ---------------------------------------------------------------------------
# 9. Display rendering (Tk-accurate)
# ---------------------------------------------------------------------------

def test_display_text_is_escape_free():
    dash = _mock_dash()
    dashboard_logs.append_log_line(
        dash, "[status 10:00:00] SearXNG search started: https://s.example.com"
    )
    for text, _tags in dash.log_text_widget.inserts:
        assert "\x1b[" not in text, f"ANSI escape found in inserted text: {text!r}"

def test_display_carries_bright_blue_tag():
    dash = _mock_dash()
    dashboard_logs.append_log_line(
        dash, "[status 10:00:00] SearXNG search started: https://s.example.com"
    )
    all_tags = [tag for _text, tags in dash.log_text_widget.inserts for tag in tags]
    assert "ansi_fg_bright_blue" in all_tags

def test_display_red_message_carries_bright_red_tag():
    dash = _mock_dash()
    dashboard_logs.append_log_line(
        dash, "[status 10:00:00] SearXNG search failed: timeout"
    )
    all_tags = [tag for _text, tags in dash.log_text_widget.inserts for tag in tags]
    assert "ansi_fg_bright_red" in all_tags

def test_normal_message_carries_no_color_tag():
    dash = _mock_dash()
    dashboard_logs.append_log_line(
        dash, "[status 10:00:00] Page 2: stored 10 rows."
    )
    color_tags = {tag for _text, tags in dash.log_text_widget.inserts for tag in tags
                  if tag.startswith("ansi_fg_")}
    assert not color_tags


# ---------------------------------------------------------------------------
# 10. History is always plain text
# ---------------------------------------------------------------------------

def test_history_stores_plain_line():
    dash = _mock_dash()
    dashboard_logs.append_log_line(
        dash, "[status 10:00:00] SearXNG search started: https://s.example.com"
    )
    assert len(dash.log_history) == 1
    assert "\x1b[" not in dash.log_history[0]

def test_history_order_preserved():
    dash = _mock_dash()
    lines = [
        "[status 10:00:00] SearXNG search started: https://s.example.com",
        "[status 10:00:01] Page 2: stored 10 rows.",
        "[status 10:00:02] Run complete: fetched 10, verified 8, retained 3 open indexes.",
    ]
    for line in lines:
        dashboard_logs.append_log_line(dash, line)
    assert list(dash.log_history) == lines


# ---------------------------------------------------------------------------
# 11. Copy All equals raw history join
# ---------------------------------------------------------------------------

def test_copy_all_equals_history_join():
    dash = _mock_dash()
    # Populate history directly (bypasses append_log_line intentionally)
    plain_status = "[status 10:00:00] SearXNG search started: https://s.example.com"
    shodan_with_ansi = "\x1b[32mShodan result\x1b[0m"
    plain_rollup_line = "✓ Scan completed: Found 3 open indexes"
    dash.log_history.extend([plain_status, shodan_with_ansi, plain_rollup_line])

    clipboard_received = []

    class _ClipParent:
        def clipboard_clear(self):
            pass
        def clipboard_append(self, text):
            clipboard_received.append(text)
        def winfo_exists(self):
            return True
        def after(self, *_args):
            pass

    dash.parent = _ClipParent()
    dashboard_logs.copy_log_output(dash)

    assert clipboard_received, "clipboard_append was never called"
    clipboard = clipboard_received[0]
    expected = "\n".join(dash.log_history)
    assert clipboard == expected
    # Pre-existing Shodan ANSI is preserved
    assert "\x1b[32m" in clipboard


# ---------------------------------------------------------------------------
# 12. Fail-open
# ---------------------------------------------------------------------------

def test_colorize_exception_renders_original_line(monkeypatch):
    dash = _mock_dash()
    monkeypatch.setattr(
        log_semantic_color, "colorize_for_display", lambda _line: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    original = "[status 10:00:00] SearXNG search started: https://s.example.com"
    dashboard_logs.append_log_line(dash, original)

    assert dash.log_history[0] == original
    inserted_texts = [text for text, _tags in dash.log_text_widget.inserts]
    full_inserted = "".join(inserted_texts)
    assert "SearXNG search started" in full_inserted
