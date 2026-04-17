# Experimental Features Dialog — Validation Report

**Date:** 2026-04-17  
**Branch:** development  
**Plan revision:** Rev 4 (approved)

---

## Automated Test Evidence

### py_compile (all modified/new files)

```
python3 -m py_compile \
  gui/dashboard/widget.py \
  gui/components/dashboard_experimental.py \
  gui/components/unified_scan_dialog.py \
  gui/components/server_list_window/window.py \
  gui/components/experimental_features_dialog.py \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/reddit_tab.py \
  gui/components/experimental_features/placeholder_tab.py \
  gui/components/experimental_features/__init__.py \
  experimental/placeholder/__init__.py
```
**Result: PASS** (no errors)

### Full test suite

```
./venv/bin/python -m pytest -q
```
**Result: 1062 passed** (baseline was 1048 before C1; 14 new tests added)

### Targeted C3-C5 suite

```
./venv/bin/python -m pytest \
  gui/tests/test_dashboard_reddit_wiring.py \
  gui/tests/test_reddit_browser_window.py \
  gui/tests/test_dashboard_scan_dialog_wiring.py \
  gui/tests/test_server_list_card4.py \
  gui/tests/test_experimental_features_dialog.py -q
```
**Result: 121 passed**

---

## File Size Impact (actual)

| File | Lines | Zone |
|------|-------|------|
| `gui/dashboard/widget.py` | 1800 | poor (pre-existing; shims only — hard rule honored) |
| `gui/components/dashboard_experimental.py` | 98 | excellent |
| `gui/components/unified_scan_dialog.py` | 1340 | good |
| `gui/components/server_list_window/window.py` | 1214 | excellent |
| `gui/components/experimental_features_dialog.py` | 143 | excellent |
| `gui/components/experimental_features/registry.py` | 52 | excellent |
| `gui/components/experimental_features/reddit_tab.py` | 79 | excellent |
| `gui/components/experimental_features/placeholder_tab.py` | 47 | excellent |
| `experimental/placeholder/__init__.py` | 13 | excellent |
| `gui/tests/test_experimental_features_dialog.py` | 299 | excellent |

---

## Locked Decisions — Compliance Check

| Decision | Status |
|----------|--------|
| Button order: `[DB Tools] [Experimental] [Config]` | PASS — enforced by Group A pack-sequence test |
| Spelling: `placeholder` (lowercase) | PASS — registry label and tab file use exact spelling |
| Preserve Reddit "Add to dirracuda DB" behavior | PASS — `add_record_callback=server_window.open_add_record_dialog` when live |
| Experimental button always visible (permanent) | PASS — unconditional pack in `_build_header_section` |
| One-time warning with persisted "Don't show again" | PASS — Group B tests cover all four cases |
| Legacy buttons removed immediately (no overlap) | PASS — C2 removed both; C3 guards prevent re-introduction |

---

## New Tests Added

### `gui/tests/test_experimental_features_dialog.py` (13 tests)

**Group A — Button order:**
- `test_experimental_button_packed_between_db_tools_and_config` — deterministic pack-sequence assertion

**C3 — Reddit tab regression guards:**
- `test_reddit_grab_callback_invoked_from_reddit_tab`
- `test_open_reddit_post_db_callback_invoked_from_reddit_tab`
- `test_reddit_tab_silent_when_no_grab_callback`
- `test_reddit_tab_silent_when_no_post_db_callback`

**C5 Group C — add-to-DB path resolution:**
- `test_open_reddit_post_db_with_live_server_window`
- `test_open_reddit_post_db_fallback_when_no_server_window`
- `test_open_reddit_post_db_treats_dead_window_as_none`
- `test_set_server_list_getter_stores_callable`

**C5 Group B — Warning-dismiss persistence:**
- `test_warning_shown_when_not_dismissed`
- `test_warning_hidden_when_already_dismissed`
- `test_dismiss_checkbox_writes_immediately_on_toggle`
- `test_dismiss_does_not_write_false_on_uncheck`

### `gui/tests/test_dashboard_scan_dialog_wiring.py` (+1 test)
- `test_show_quick_scan_dialog_does_not_pass_reddit_grab_callback` — key-absence assertion

### `gui/tests/test_server_list_card4.py` (+1 test in new class)
- `TestHeaderCommandsDoNotOpenRedditBrowser.test_create_header_button_commands_do_not_open_reddit_browser`

---

## Manual HI Checks

| # | Check | Status |
|---|-------|--------|
| 1 | Button order: `[DB Tools] [Experimental] [Config]` left-to-right | PENDING |
| 2 | First open: warning banner + "Don't show again" checkbox visible | PENDING |
| 3 | Dismiss — same session: check → close → reopen → no warning | PENDING |
| 4 | Dismiss — after restart: check → restart → reopen → no warning | PENDING |
| 5 | Reddit Grab (idle): Experimental → Reddit → Open Reddit Grab → grab dialog appears | PENDING |
| 6 | Reddit Grab (scanning): during active scan → grab dialog does NOT appear | PENDING |
| 7 | Reddit Post DB (server list open): server list first → Experimental → Open Reddit Post DB → browser opens as child of server list; Add to dirracuda DB → Add Record dialog | PENDING |
| 8 | Reddit Post DB (no server list): Experimental → Open Reddit Post DB → browser opens with dashboard parent; Add to dirracuda DB → native "Not available" dialog | PENDING |
| 9 | No legacy button — Start Scan: open Start Scan → no "Reddit Grab (EXP)" button | PENDING |
| 10 | No legacy button — Server List: open Server List → no "Reddit Post DB (EXP)" in header | PENDING |
| 11 | Theme toggle: switch theme → Experimental button reflects updated colors | PENDING |
