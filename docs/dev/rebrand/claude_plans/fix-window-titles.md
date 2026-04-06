# Fix: xsmbseek Window Titles Still Read SMBSeek

## Context

Pass 3 rebrand is complete. The verification step revealed that `xsmbseek` (the launcher) sets the main window title to `"xsmbseek - SMBSeek GUI Frontend"` — these three `.title()` calls were missed during the earlier passes.

---

## Changes: `xsmbseek` (3 lines)

| Line | Old | New |
|------|-----|-----|
| 260 | `dialog.title("SMBSeek Setup Required")` | `dialog.title("Dirracuda Setup Required")` |
| 622 | `self.root.title("xsmbseek - SMBSeek GUI Frontend")` | `self.root.title("Dirracuda")` |
| 639 | `self.root.title(f"xsmbseek - SMBSeek GUI Frontend ({smbseek_path.name})")` | `self.root.title(f"Dirracuda ({smbseek_path.name})")` |

---

## (Previous pass 3 plan — completed)

### Group A: User-Visible Runtime Strings (highest priority)

### `gui/components/dashboard.py`
| Line | Old | New |
|------|-----|-----|
| 327 | `"SMBSeek Security Toolkit"` (main dashboard title label) | `"Dirracuda"` |

### `gui/components/app_config_dialog.py`
| Line | Old | New |
|------|-----|-----|
| 59 | `"smbseek": "SMBSeek Root"` (dict value only — key stays) | `"smbseek": "Dirracuda Root"` |
| 61 | `"config": "SMBSeek Config"` | `"config": "Dirracuda Config"` |
| 230 | `text="Edit SMBSeek Config..."` (button label) | `text="Edit Dirracuda Config..."` |

### `gui/components/database_setup_dialog.py`
| Line | Old | New |
|------|-----|-----|
| 148 | `"Select an existing SMBSeek database file\n..."` | `"Select an existing Dirracuda database file\n..."` |
| 164 | `"Close SMBSeek GUI without\nsetting up a database."` | `"Close Dirracuda without\nsetting up a database."` |
| 318 | `title="Select SMBSeek Database File"` | `title="Select Dirracuda Database File"` |
| 356 | `"Exit SMBSeek"` (messagebox title) | `"Exit Dirracuda"` |

### `gui/components/db_tools_dialog.py`
| Line | Old | New |
|------|-----|-----|
| 277 | `title="Select SMBSeek Database or CSV to Import"` | `title="Select Dirracuda Database or CSV to Import"` |

### `gui/components/data_import_dialog.py`
| Line | Old | New |
|------|-----|-----|
| 128 | `"Import CSV/JSON data files exported from SMBSeek"` | `"Import CSV/JSON data files exported from Dirracuda"` |

### `gui/demo.py`
| Line | Old | New |
|------|-----|-----|
| 43 | `"SMBSeek GUI Demo"` (window title) | `"Dirracuda Demo"` |

### `gui/main.py`
| Line | Old | New |
|------|-----|-----|
| 205 | (error dialog referencing SMBSeek backend — verify exact text) | replace "SMBSeek" with "Dirracuda" |
| 415 | (error message referencing SMBSeek GUI — verify exact text) | replace "SMBSeek" with "Dirracuda" |

### `gui/utils/data_export_engine.py`
| Line | Old | New |
|------|-----|-----|
| 223 | `'tool': 'SMBSeek GUI'` (embedded in exported JSON files) | `'tool': 'Dirracuda'` |
| 271 | CSV header: `"# SMBSeek Export - ..."` | `"# Dirracuda Export - ..."` |

### `gui/utils/scan_manager.py`
| Line | Old | New |
|------|-----|-----|
| 223 | `"created_by": "SMBSeek GUI"` (stored in database) | `"created_by": "Dirracuda"` |

### `gui/utils/error_codes.py`
| Lines | Old | New |
|-------|-----|-----|
| 142 | `"Use a complete SMBSeek database..."` | `"Use a complete Dirracuda database..."` |
| 148 | `"Import only compatible SMBSeek databases..."` | `"Import only compatible Dirracuda databases..."` |
| 154 | `"Verify the database was created by SMBSeek toolkit"` | `"Verify the database was created by Dirracuda"` |

### `gui/utils/backend_interface/config.py`
| Line | Old | New |
|------|-----|-----|
| 35 | `"SMBSeek configuration template not found"` | `"Dirracuda configuration template not found"` |

### `gui/utils/backend_interface/error_parser.py`
| Lines | Old | New |
|-------|-----|-----|
| 39–40 | `"SMBSeek backend is missing required SMB libraries..."` | `"Dirracuda backend is missing required SMB libraries..."` |

### `shared/quarantine.py`
| Line | Old | New |
|------|-----|-----|
| 23 | `"This directory stores quarantined SMBSeek artifacts."` | `"This directory stores quarantined Dirracuda artifacts."` |

---

## Group B: SMB Client Network Identifiers

These strings are sent to remote SMB servers as the client name during probe/extract operations — externally visible.

| File | Line | Old | New |
|------|------|-----|-----|
| `gui/utils/probe_runner.py` | 22 | `DEFAULT_CLIENT_NAME = "xsmbseek-probe"` | `DEFAULT_CLIENT_NAME = "dirracuda-probe"` |
| `gui/utils/extract_runner.py` | 26 | `DEFAULT_CLIENT_NAME = "xsmbseek-extract"` | `DEFAULT_CLIENT_NAME = "dirracuda-extract"` |

---

## Group C: User-Facing Release Docs

### `docs/release/XSMBSEEK_CHANGELOG.md`
- L1: `# SMBSeek GUI Changelog` → `# Dirracuda Changelog`
- L3: `All notable changes to the SMBSeek GUI project` → `All notable changes to Dirracuda`
- Remaining inline `SMBSeek` references in changelog entries → replace with `Dirracuda`

### `docs/release/2026-01-17.md`
- L1: `# SMBSeek GUI Release Notes (Draft) – 2026-01-17` → `# Dirracuda Release Notes (Draft) – 2026-01-17`

### `gui/.github/ISSUE_TEMPLATE/bug_report.md`
| Line | Old | New |
|------|-----|-----|
| 3 | `about: Create a report to help us improve the SMBSeek GUI` | `about: Create a report to help us improve Dirracuda` |
| 28 | `SMBSeek GUI version: [e.g. 1.0.0]` | `Dirracuda version: [e.g. 1.0.0]` |

---

## Group D: Module Docstrings (consistency sweep)

These are not runtime-visible but complete the branding cleanup. Each is a one-line docstring change.

| File | Old text | New text |
|------|----------|----------|
| `shared/__init__.py` L2 | `SMBSeek Shared Utilities` | `Dirracuda Shared Utilities` |
| `shared/__init__.py` L4 | `Shared functionality used across all SMBSeek command modules` | `...all Dirracuda command modules` |
| `shared/config.py` L105 | `Centralized configuration management for SMBSeek.` | `...for Dirracuda.` |
| `shared/config.py` L611 | `Convenience function to load SMBSeek configuration.` | `...Dirracuda configuration.` |
| `tools/__init__.py` L2 | `SMBSeek Tools Module` | `Dirracuda Tools Module` |
| `tools/__init__.py` L4 | `This module contains all SMBSeek security analysis tools` | `...Dirracuda...` |
| `tools/__init__.py` L26 | `__author__ = "SMBSeek Development Team"` | `"Dirracuda Development Team"` |
| `tools/db_query.py` L3 | `SMBSeek Database Query Utility` | `Dirracuda Database Query Utility` |
| `tools/db_maintenance.py` L23 | `Database maintenance utilities for SMBSeek SQLite database.` | `...Dirracuda SQLite database.` |
| `tools/db_import.py` L22 | `Import utility for migrating existing SMBSeek data files...` | `...Dirracuda data files...` |
| `tools/cleanup_duplicate_shares.py` L3 | `SMBSeek Database Cleanup: Remove Duplicate Share Entries` | `Dirracuda Database Cleanup: ...` |
| `tools/cleanup_duplicate_shares.py` L5 | `This script performs a one-time cleanup of the SMBSeek database` | `...Dirracuda database` |
| `tools/db_manager.py` L28 | `Thread-safe SQLite database manager for SMBSeek toolkit.` | `...Dirracuda toolkit.` |
| `tools/db_manager.py` L31 | `Follows the established SMBSeek architecture patterns.` | `...Dirracuda architecture patterns.` |
| `tools/db_manager.py` L241 | `High-level data access layer for SMBSeek operations.` | `...Dirracuda operations.` |
| `tools/db_manager.py` L243 | `...following SMBSeek data patterns.` | `...Dirracuda data patterns.` |
| `tools/add_share_summary_view.py` L3 | `SMBSeek Database Enhancement: Add Share Summary View` | `Dirracuda Database Enhancement: ...` |
| `tools/add_share_uniqueness_constraint.py` L3 | `SMBSeek Database Migration: Add Share Uniqueness Constraint` | `Dirracuda Database Migration: ...` |
| `commands/__init__.py` L2 | `SMBSeek Command Modules` | `Dirracuda Command Modules` |
| `commands/__init__.py` L4 | `...for the unified SMBSeek CLI.` | `...unified Dirracuda CLI.` |
| `commands/discover/__init__.py` L2 | `SMBSeek Discover Package` | `Dirracuda Discover Package` |
| `commands/discover/operation.py` L2 | `SMBSeek Discover Operations` | `Dirracuda Discover Operations` |
| `commands/access/__init__.py` L2 | `SMBSeek Access Package` | `Dirracuda Access Package` |
| `commands/access/operation.py` L2 | `SMBSeek Access Operations` | `Dirracuda Access Operations` |
| `commands/collect.py` L3,5,8 | `SMBSeek Collect/3.0.0/command` | `Dirracuda Collect/3.0.0/command` |
| `commands/analyze.py` L3,5,8 | `SMBSeek Analyze/3.0.0/command` | `Dirracuda Analyze/3.0.0/command` |
| `commands/database.py` L3,5,8 | `SMBSeek Database Command/3.0.0` | `Dirracuda Database Command/3.0.0` |
| `gui/components/__init__.py` L1 | `# SMBSeek GUI Components Package` | `# Dirracuda GUI Components Package` |
| `gui/components/scan_dialog.py` L2 | `SMBSeek Scan Dialog` | `Dirracuda Scan Dialog` |
| `gui/components/app_config_dialog.py` L2,4 | `SMBSeek Application Configuration Dialog` / `for managing xsmbseek integration settings.` | `Dirracuda Application Configuration Dialog` / `for managing Dirracuda integration settings.` |
| `gui/components/dashboard.py` L2,56 | `SMBSeek Mission Control Dashboard` / `key SMBSeek metrics` | `Dirracuda Mission Control Dashboard` / `key Dirracuda metrics` |
| `gui/components/data_import_dialog.py` L2 | `SMBSeek GUI - Data Import Dialog` | `Dirracuda - Data Import Dialog` |
| `gui/components/scan_results_dialog.py` L2 | `SMBSeek Scan Results Dialog` | `Dirracuda Scan Results Dialog` |
| `gui/components/image_viewer_window.py` L2 | `Read-only image viewer for xsmbseek.` | `Read-only image viewer for Dirracuda.` |
| `gui/components/file_viewer_window.py` L2 | `Read-only file viewer for xsmbseek` | `Read-only file viewer for Dirracuda` |
| `gui/components/ftp_server_picker.py` L2 | `FTP server picker dialog for xsmbseek.` | `FTP server picker dialog for Dirracuda.` |
| `gui/demo.py` L3,5 | `SMBSeek GUI Demo Script` / `key features of the SMBSeek GUI` | `Dirracuda Demo Script` / `key features of Dirracuda` |
| `gui/main.py` L47,58 | `Main SMBSeek GUI application.` / `Initialize SMBSeek GUI application.` | `Main Dirracuda application.` / `Initialize Dirracuda application.` |
| `gui/utils/__init__.py` L1 | `# SMBSeek GUI Utilities Package` | `# Dirracuda GUI Utilities Package` |
| `gui/utils/data_export_engine.py` L2,23 | `SMBSeek GUI - Data Export Engine` / `...for SMBSeek GUI.` | `Dirracuda - Data Export Engine` / `...for Dirracuda.` |
| `gui/utils/data_import_engine.py` L2,34 | `SMBSeek GUI - Data Import Engine` / `...for SMBSeek GUI.` | `Dirracuda - Data Import Engine` / `...for Dirracuda.` |
| `gui/utils/database_access.py` L2,28,30 | `SMBSeek Database Access Layer` etc. | `Dirracuda Database Access Layer` etc. |
| `gui/utils/db_tools_engine.py` L2,187 | `SMBSeek GUI - Database Tools Engine` / `...for SMBSeek GUI.` | `Dirracuda - Database Tools Engine` / `...for Dirracuda.` |
| `gui/utils/default_gui_settings.py` L2 | `Default GUI settings for SMBSeek.` | `Default GUI settings for Dirracuda.` |
| `gui/utils/error_codes.py` L2 | `SMBSeek GUI - Centralized Error Code System` | `Dirracuda - Centralized Error Code System` |
| `gui/utils/logging_config.py` L1 | `Logging configuration for SMBSeek GUI.` | `Logging configuration for Dirracuda.` |
| `gui/utils/scan_manager.py` L2 | `SMBSeek Scan Manager` | `Dirracuda Scan Manager` |
| `gui/utils/settings_manager.py` L2,4,27 | `SMBSeek GUI - Settings Manager` etc. | `Dirracuda - Settings Manager` etc. |
| `gui/utils/backend_interface/interface.py` L2,36 | `SMBSeek Backend Interface` / `...SMBSeek backend` | `Dirracuda Backend Interface` / `...Dirracuda backend` |
| `docs/example_db/generate_bogan_dbs.py` L3 | `Generate bogan test databases for SMBSeek import testing.` | `...Dirracuda import testing.` |
| `requirements.txt` L1 | `# SMBSeek Unified Toolkit Dependencies` | `# Dirracuda Dependencies` |

---

## Out of Scope

- `docs/dev/` — internal planning documents, task cards, refactor notes
- Tkinter style names: `"SMBSeek.Horizontal.TProgressbar"`, `"SMBSeek.TButton"` — internal, not user-visible
- Class names: `SMBSeekOutput`, `SMBSeekTheme`, `SMBSeekConfig`, etc.
- Database schema defaults: `smbseek_unified`, `tool_name='smbseek'`
- `cli/smbseek.py` argparse description/version — prior decision
- `gui/utils/style.py` L638, L756, L758, L764 — style name strings (internal)
- `gui/utils/probe_runner.py` L340 `("xsmbseek", "pry", ...)` tuple key — internal state identifier (verify before changing)
- CLAUDE.md intentional historical references

---

## Verification

Launch `./xsmbseek` — title bar should read `"Dirracuda"` (or `"Dirracuda (path)"` when a backend path is configured). Database setup dialog should say `"Dirracuda Setup Required"`.
