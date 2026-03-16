Codebase Overview and Current Structure

Project Structure: The SMBSeek project is organized into several top-level directories and entry-point scripts. Notably:

smbseek – The main CLI script (no .py extension) that parses arguments and runs the unified workflow

. This is the command-line entry point.

xsmbseek – The GUI launcher script, creating the Tkinter app

. It includes GUI configuration management (XSMBSeekConfig) and the main GUI class (XSMBSeekGUI) in one file

.

commands/ – Contains backend operation modules for various phases (e.g. discover.py, access.py). Some legacy CLI command scripts (like run.py, report.py) are also here but marked deprecated

.

shared/ – Shared utilities and core logic used across CLI and GUI. This includes configuration loading, database interface, output formatting, etc (e.g. config.py, database.py, output.py, workflow.py). These support the unified CLI workflow

.

gui/ – All GUI-specific code. It has subdirectories:

gui/components/ for UI windows/dialogs (e.g. dashboard.py, server_list_window/, etc.),

gui/utils/ for non-UI logic supporting the GUI (e.g. backend interface, settings manager, runners for probe/extract operations).

tools/ – Ancillary scripts (database import/export, schema, etc.), e.g. db_import.py, db_query.py, db_manager.py. Some core database classes (like DatabaseManager) reside here and are imported via path hacks

.

signatures/ – Data for RCE vulnerability checks (YAML rule files and loader code).

Overall, the directory layout is logical but somewhat fragmented. The separation between commands and shared is not clearly defined (both hold backend logic), and there’s overlap. The GUI code is modularized into components and utilities, but the backend code is split across commands, shared, and tools, requiring manual sys.path manipulation for imports

. This indicates the project isn’t structured as a standard Python package, which complicates internal imports.

Key Functional Areas & Modules:

CLI Unified Workflow: The CLI uses shared/workflow.py which defines a UnifiedWorkflow orchestrator that calls into commands.discover.DiscoverOperation and commands.access.AccessOperation for the two main steps

. Config and output handling are done via shared.config.SMBSeekConfig and shared.output.SMBSeekOutput. The CLI entry script smbseek parses args (like --country, --force-hosts) with validation helpers

and then invokes the workflow.

Backend Operations:

commands/discover.py (Shodan scanning & auth testing) – a monolithic module (~1,308 lines) implementing Shodan queries, SMB login attempts, host filtering, etc. It defines a DiscoverOperation class and related helpers

. It also defines a DiscoverResult dataclass (duplicated elsewhere)

.

commands/access.py (share enumeration & RCE checks) – another large module (~1,034 lines) with class AccessOperation for testing share access on hosts and optional RCE vulnerability analysis

. It similarly defines an AccessResult dataclass (also duplicated)

. Both modules directly call out to system tools (e.g. smbclient) and use threads/pools for concurrency.

Other command modules: Some are stubs (e.g. commands/run.py and report.py are pure deprecation warnings

). There may be smaller ones (like commands/test_access_nt_status.py, which is a test stub for AccessOperation

).

Database and Config: shared/database.py wraps an SQLite database with higher-level methods for filtering and tracking scan sessions

. It relies on tools/db_manager.py (which defines low-level DB access and schema management). Config is handled by shared/config.py (loads conf/config.json with default fallbacks). The config structure is comprehensive, covering Shodan settings, workflow toggles, timeouts, etc

.

Output and Quarantine: shared/output.py defines SMBSeekOutput for uniform console messages (with color icons for info/warning/etc.)

and an SMBSeekReporter for summary reports

. shared/quarantine.py likely handles file quarantine storage (not inspected in detail).

GUI Application: The GUI is launched via xsmbseek. Internally, gui/main.py provides an alternate entry (SMBSeekGUI class) when running GUI inside the same process (though currently xsmbseek seems to be the main path). The GUI architecture separates front-end from back-end: the GUI code calls the backend by spawning the CLI as a subprocess via BackendInterface (to keep a clean separation)

. Key GUI modules:

Dashboard (gui/components/dashboard.py): Implements the main window’s “mission control” view with status cards, progress, and buttons to open other dialogs

. This file is large (~2,100 lines) encompassing UI layout, real-time log display, and controlling scans.

Server List (gui/components/server_list_window/window.py): The largest module (~2,781 lines), managing the table of discovered servers with filtering, multi-select, right-click actions (Probe, Browse, Extract, Pry), and integration with other dialogs

. It acts as a controller tying together subcomponents for filters, details, export, etc., which are broken out into separate modules (e.g. server_list_window/export.py, .../filters.py, .../details.py, .../table.py).

Supporting Dialogs: Many other GUI components exist (scan dialogs, config editor, file browser, “Pry” password dialog, etc.), each in its own module. These seem reasonably sized, while the Dashboard and ServerList remain quite complex.

GUI Utilities:

gui/utils/backend_interface/ – Implements BackendInterface which wraps CLI calls in threads, parses output, and reports progress to the GUI

. It is another large file (~940 lines) coordinating subprocess execution and interpreting results (with help from submodules like progress, process_runner, mock_operations).

gui/utils/settings_manager.py – Manages persistent GUI settings (window positions, user preferences) in ~/.smbseek/gui_settings.json. This is ~933 lines, including a large default settings schema in code

.

gui/utils/probe_runner.py and extract_runner.py – These handle background threads for the “Probe” and “Extract” operations in the GUI, using impacket or smbclient to enumerate files and download content. They are more modest in size (~200 lines each) and return results to the GUI

.

Various others: e.g. template_store.py for saving filter templates, scan_manager.py to coordinate scans, etc.

Summary of Observations: The codebase is feature-rich but exhibits several very large modules that combine many responsibilities. For instance, commands/discover.py handles everything from Shodan querying to SMB connection pooling in one file

. On the GUI side, the Server List window module “orchestrates all server list functionality” in one place, and the Dashboard does similarly for the main view. There is also some duplicate code/definitions (e.g. DiscoverResult and AccessResult dataclasses are defined in both the shared/workflow.py and their respective command modules). The presence of deprecated command files and sys.path insertion hacks suggests the structure could be cleaned up for clarity and best practices. Overall, there’s an opportunity to refactor for better modularity: splitting large files into cohesive units, consolidating duplicate or obsolete code, and possibly reorganizing the package layout to eliminate the ad-hoc import handling.
