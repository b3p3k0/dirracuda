# Database Consolidation and Tools Overhaul

Dirracuda maintains multiple SQLite databases: the main Dirracuda database and several sidecar databases used by experimental features.  The dashboard currently exposes separate buttons for “DB Viewer” and “DB Tools,” and sidecar databases are only accessible through experimental feature UIs.  This document proposes consolidating database functions into a single dashboard button and unifying data storage to simplify management.

## 1. Rationale

* **Multiple DBs cause confusion:** Users may not realise that SearXNG and Reddit searches store results in sidecar DBs at `~/.dirracuda/data/experimental/se_dork.db`【119086241037487†L0-L70】 and `~/.dirracuda/data/experimental/reddit_od.db`【40863413331438†L0-L63】.  These DBs cannot be viewed or managed from the main DB viewer.
* **Redundant actions:** Having separate buttons for “View DB” and “DB Tools” leads to duplication of logic and UI clutter.  A single entry point would streamline access and reduce maintenance overhead.
* **Migrating experimental features to core:** As SearXNG and Reddit are promoted, their results will be stored in the main DB.  The sidecar DBs become legacy data and should be accessible but clearly marked as such.

## 2. Single “DB” dashboard button

Replace the two existing dashboard buttons (“DB Viewer” and “DB Tools”) with a single **Database** button.  Clicking this button opens a modal dialog presenting three options:

1. **View Dirracuda DB** – opens the current DB viewer showing the primary `search_results`, `scans`, `assets` and other tables.  The viewer should allow filtering, sorting, and exporting entries.
2. **Database Tools** – opens the existing DB tools panel (e.g., vacuum, integrity check, backup/restore).  This panel can be re‑organised to fit the normalised UI guidelines but retains its functionality.
3. **View Sidecar DBs** – lists any legacy sidecar databases found in `~/.dirracuda/data/experimental`.  For each DB, display its name and a short description of its origin (e.g., “SearXNG Dork Results,” “Reddit Ingestion Targets”).  Users can open these DBs in a read‑only viewer or run import operations to move data into the main DB.

Provide tool tips explaining each option so that users understand the difference between the main database and sidecar databases.

## 3. Unified DB viewer enhancements

The current DB viewer primarily lists assets and scan results.  To support the integration of search providers:

* **Add a `search_results` view** that displays entries from the new unified `search_results` table.  Columns should include provider, target, protocol, host, port, score/confidence, classification, probe status, and timestamps.  Implement filtering by provider and search text.
* **Enable cross‑DB import:** When opening a sidecar DB from the “View Sidecar DBs” section, provide a button labelled “Import into Main DB.”  This triggers a migration script that reads sidecar tables (e.g., `dork_results`, `reddit_targets`), converts them to the unified schema, inserts them into the main DB, and marks the sidecar DB as imported.  Show a summary of imported rows and any conflicts (e.g., duplicates) after completion.
* **Mark legacy entries:** For entries imported from a sidecar DB, include a tag or column (e.g., `legacy_source`) indicating the original DB.  This aids auditing and debugging.

## 4. DB tools integration

The existing DB tools allow vacuum, integrity checks, and backups.  These should remain available but be launched from within the unified “Database” dialog.  Consider grouping tool actions into categories (maintenance, backup, advanced) and provide warnings where necessary (e.g., “Integrity check may take several minutes”).

## 5. Implementation notes

* The single DB button can be implemented by modifying `gui/dashboard/widget.py`.  Replace the separate button initialisers with one button labelled “Database” and wire it to a new method `_show_database_dialog()`.
* The dialog can be built using the normalised UI template described in `ui_normalization.md` to ensure consistency.
* When scanning sidecar directories, use `os.listdir` to find `.db` files under `~/.dirracuda/data/experimental`.  Read their schema to determine which provider they belong to (e.g., presence of `dork_results` implies SearXNG).  Populate the sidecar list accordingly.
* Keep the ability to open sidecar DBs in read‑only mode using the existing result browsers (`se_dork_browser_window.py`, `reddit_browser_window.py`) but provide a banner indicating they are deprecated.  Eventually, these browsers can be replaced with a unified viewer.

By consolidating database tools and viewer functionality, Dirracuda will provide a clearer and more manageable database interface, reduce user confusion, and facilitate migration away from legacy sidecar databases.