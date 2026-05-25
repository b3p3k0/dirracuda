# Overview of Planned Overhauls for Dirracuda (Development Branch)

Dirracuda’s current development branch contains a mixture of stable “core” features and experimental modules.  Major overhauls are planned to promote certain experimental features, reorganise the user interface (UI), and establish a consistent internal architecture.  This document summarises the high‑level goals of these changes so that the planning agent (PA) can translate them into specific development tasks.

## Context

1. **Experimental search modules** – The SearXNG dork search and Reddit ingestion (Redseek) modules live under `experimental/` and write results to separate sidecar SQLite databases.  For example, the dork search stores runs and results in a sidecar DB at `~/.dirracuda/data/experimental/se_dork.db` with tables `dork_runs` and `dork_results`【119086241037487†L0-L70】.  Redseek keeps its posts and extracted targets in `~/.dirracuda/data/experimental/reddit_od.db` with tables `reddit_posts` and `reddit_targets`【40863413331438†L0-L63】.  These modules currently use bespoke UIs and logic separate from the core scanning workflow.
2. **Experimental feature registry** – The file `gui/components/experimental_features/registry.py` lists the available experimental features: SearXNG, Reddit, Dorkbook and Keymaster【567549463675262†L20-L59】.  Only SearXNG and Reddit provide real search functionality; Dorkbook and Keymaster are “accessories”.
3. **Current start‐scan dialog** – The “Start Scan” button launches a Shodan‑centric dialog with protocol checkboxes (SMB, FTP, HTTP) and Shodan‑specific fields (max results, API key prompt, etc.).  There is no way to combine multiple providers in a single scan and the styling of this dialog differs from the rest of the UI.

## Primary Goals

The overhaul aims to:

- **Promote SearXNG, Censys and Reddit ingestion to core search providers.**  Their code will move from `experimental/` into a permanent package and their results will be stored in the main Dirracuda database instead of sidecar DBs.  A one‑time import from existing sidecar DBs should be offered to prevent data loss.
- **Unify the start‑scan flow.**  The “Start Scan” button will open a single dialog where users can tick one or more search providers (Shodan, SearXNG, Censys, Reddit, etc.).  Each provider will have its own collapsible options panel for queries, API keys and provider‑specific settings.  When the scan is launched, each provider will stream results into the live‑scan window in a consistent format.
- **Normalise the UI.**  The Shodan scan dialog currently has unique styling; it should be revised to match the simpler, cleaner style used elsewhere.  All provider dialogs should share common controls and spacing to create a cohesive user experience.
- **Consolidate database interactions.**  A single “DB” button on the dashboard should provide options to view the main Dirracuda DB, run DB tools, or access sidecar DBs left over from experimental features.  The new DB viewer should allow users to import sidecar results into the main DB.
- **Replace the experimental button with an “Accessories” section.**  Non‑core modules such as Dorkbook, WebUI and Keymaster should live under this menu, keeping the dashboard uncluttered.  Search providers that have graduated to core status should no longer appear here.
- **Refine configuration management.**  Shodan‑specific configuration fields (like API key and default search parameters) should be removed from the global config panel and moved into the options panel for the Shodan provider.  Each provider should manage its own settings.
- **Define a standard integration language.**  To reduce friction when adding new providers in the future, a common interface will be defined.  Each provider will implement methods such as `start_scan`, `yield_results`, `probe_target`, and `integrate_with_db` so that the rest of the application (probe, extract, classification, live‑scan window) can interact with them consistently.

## Desired Outcomes

* **Improved user experience:** Users can choose and configure multiple search providers from a single dialog, see results in one place, and access database tools through a unified interface.
* **Extensible architecture:** A clear, provider‑agnostic interface makes it easier to add new search modules like Censys or others without hacking in special cases.
* **Cleaner codebase:** Removing Shodan‑specific assumptions from core code and eliminating redundant sidecar databases reduces complexity and technical debt.

These goals will be elaborated in dedicated planning documents for each area (start‑scan, UI normalisation, database consolidation, accessories restructuring, configuration revision, and provider integration).  Please read those documents for detailed requirements and suggested implementation steps.