# Implementation Plan Summary

This document summarises the tasks and milestones required to realise the overhaul described in the accompanying planning documents.  It provides a high‑level roadmap for the local planning agent (PA) and development team.

## Phase 1: Project setup and code relocation

1. **Set up provider package** – Create `dirracuda/providers` and subdirectories for `shodan`, `searxng`, `reddit`, and `censys`.  Move the existing Shodan code to `providers/shodan`, SearXNG dork code to `providers/searxng`, and Reddit ingestion code to `providers/reddit`.  Establish placeholder module for `providers/censys` with skeleton classes.
2. **Update imports** – Refactor all references from `experimental/se_dork` and `experimental/redseek` to the new provider paths.  Remove experimental prefixes from configuration names and UI labels.
3. **Add unified data models** – Implement the `SearchResult` dataclass and provider base classes described in `search_provider_integration.md`.  Modify providers to emit `SearchResult` objects and implement the required methods.
4. **Create `search_results` table** – Add a migration script to create the unified `search_results` table in the main DB.  Add a `scans` table if not already present to track multi‑provider scans.

## Phase 2: UI redesign

1. **Implement BaseDialog and UI helpers** – Create reusable dialog classes and form field helpers as described in `ui_normalization.md`.  Refactor existing dialogs (e.g., Shodan quick scan, SearXNG tab, Reddit grab dialog) to use these templates.
2. **Develop new start‑scan dialog** – Build the unified start‑scan dialog with provider selection and collapsible options panels.  Integrate it into the dashboard and hide the old quick scan dialog behind a “classic scan” option.
3. **Add Database dialog** – Replace the DB viewer and DB tools buttons with a single “Database” button.  Implement the three‑option dialog and update the DB viewer to support `search_results` and sidecar import.
4. **Introduce Accessories menu** – Replace the “Experimental” button with an “Accessories” button that loads Dorkbook, WebUI and Keymaster from a dynamic registry.

## Phase 3: Provider integration and migration

1. **SearXNG and Reddit migration** – Update the SearXNG and Reddit providers to store results in the `search_results` table and remove sidecar DB writes.  Implement one‑time import routines to migrate existing data from `se_dork.db` and `reddit_od.db`.
2. **Censys implementation** – Develop the Censys provider to support IP searches.  Integrate API authentication and error handling.  Add its options panel to the start‑scan dialog.
3. **Remove experimental features** – After confirming successful migration, mark the `experimental/` directory as deprecated.  Adjust the build process to exclude it from future releases.

## Phase 4: Standard language and event system

1. **Event dispatcher** – Implement a simple event dispatcher and define events (`ScanStarted`, `ResultFound`, `ProbeCompleted`, etc.).  Modify the `ScanManager` and providers to emit and consume these events.
2. **Modify live‑scan window** – Subscribe the live‑scan window to result events.  Tag results with provider names and update the UI in real time.
3. **Integrate probes** – Ensure that provider results invoke the shared probe functions.  Update the DB with probe outcomes and classification results.

## Phase 5: Configuration overhaul

1. **Provider‑specific configs** – Introduce `ProviderConfig` classes and modify the configuration file structure.  Add UI for editing provider configs from the accessories menu.
2. **Remove global Shodan config** – Clean up the configuration dialog to remove Shodan‑specific fields.  Redirect users to the provider options panel for editing Shodan settings.
3. **Migrations and versioning** – Add a config version number and migration scripts to transition old configs to the new structure.  Implement fallback logic if the config version is missing.

## Milestones and deliverables

| Milestone | Deliverable |
| --- | --- |
| **M1** – Code base reorganised | Provider packages created; code relocated; unified data model defined; DB schema migration written |
| **M2** – Unified UI prototypes | Base dialog template; new start‑scan dialog and database dialog prototypes; accessories menu implemented |
| **M3** – Provider integration complete | SearXNG and Reddit integrated into main DB; Censys provider functional; sidecar migration scripts working |
| **M4** – Event system live | Event dispatcher integrated; live‑scan window updated; providers emit events |
| **M5** – Configuration overhaul | Provider‑specific configs implemented; global Shodan settings removed; configuration migration completed |

Throughout these phases, ensure that unit tests are updated and extended to cover new functionality, migrations, and UI behaviours.  A staged rollout with feature flags may help mitigate risk when introducing large changes.