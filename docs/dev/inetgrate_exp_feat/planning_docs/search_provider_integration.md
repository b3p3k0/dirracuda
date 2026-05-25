# Search Provider Integration and Promotion

This document outlines the steps required to promote the existing SearXNG dork search and Reddit ingestion modules to core search providers, prepare for the addition of a Censys provider, and establish a common interface for future integrations.  The goal is to move these providers out of the `experimental/` directory, unify their data models and storage with the main Dirracuda database, and ensure they can participate in scanning workflows alongside the existing Shodan provider.

## 1. Relocate SearXNG and Reddit code

* **Create a top‑level package for providers.**  A new directory such as `dirracuda/providers/` should be added to house search providers.  Each provider will live in its own subpackage (`searxng`, `reddit`, `censys`, `shodan`, etc.).  This follows the existing pattern for other components and removes the experimental connotation.
* **Move code from experimental modules.**  The current SearXNG dork logic resides in `experimental/se_dork` and uses a sidecar DB for `dork_runs` and `dork_results`【119086241037487†L0-L70】.  The Reddit ingestion lives in `experimental/redseek` with its own DB tables `reddit_posts` and `reddit_targets`【40863413331438†L0-L63】.  These packages should be relocated under `dirracuda/providers/searxng` and `dirracuda/providers/reddit`, respectively.  When moving, update import paths and module names.
* **Deprecate sidecar DBs.**  Both providers should write directly to the main Dirracuda database.  For existing users, implement a one‑time migration that checks the sidecar DB and offers to import runs/results into the main DB.  Use the existing `probe_status`, `verdict`, and other fields to populate the unified `search_results` table (to be defined below).

## 2. Add a Censys provider

Censys will be promoted alongside SearXNG and Reddit even though no implementation exists yet.  To prepare:

* **Design a new provider module** `dirracuda/providers/censys`.  This module will implement the common provider interface (see section 4) using the Censys API.  It should handle authentication (via API ID/secret) and query execution.  Initially it can support IPv4 search; additional asset types (domains, certificates) can be added later.
* **Configuration options** should include API credentials, query string, result limit, and filters (e.g., country/region).  Place these controls in the provider’s options panel of the new start‑scan dialog.

## 3. Unify data models and storage

* **Define a `SearchResult` dataclass** shared by all providers.  Fields should cover at least:
  - `provider`: identifier of the originating provider (e.g., `shodan`, `searxng`, `censys`, `reddit`).
  - `target_raw`: original item (URL, IP, domain) returned by the provider.
  - `protocol`/`service`: classification of the target (e.g., `ftp`, `http`, `smb`, `reddit_user`).
  - `host`: hostname or IP.
  - `port`: optional integer port.
  - `title` and `snippet`: optional context from the provider (e.g., page title or post preview).
  - `score` or `confidence`: provider‑specific relevance score.
  - `probe_status`, `probe_indicator_matches`, `classification`, etc., aligning with the fields currently stored in the dork and reddit sidecar DBs【119086241037487†L0-L70】【40863413331438†L0-L63】.
* **Create a central `search_results` table** in the main database to store instances of `SearchResult`.  This table should replace the `dork_results` and `reddit_targets` tables and include a foreign key to the `scans` table (for results associated with a scan).  Unique constraints will prevent duplicate entries.
* **Implement migration scripts** to read from `experimental/se_dork.db` and `experimental/reddit_od.db`, convert each row to the new schema, and insert into `search_results`.  After migration, mark the sidecar DB as deprecated but leave it accessible via the new DB viewer for legacy reference.

## 4. Define a provider interface

To avoid ad‑hoc integration, all providers should implement a common interface.  Suggested methods include:

1. `start_scan(options: ProviderOptions) -> ProviderHandle` – kick off a scan with provider‑specific options.  Return a handle that can be used to check status or cancel.
2. `yield_results(handle: ProviderHandle) -> Iterator[SearchResult]` – return an iterator (or async generator) that yields `SearchResult` objects as they are discovered.  Each result should be written to the main DB and forwarded to the live‑scan window.
3. `stop_scan(handle: ProviderHandle)` – gracefully stop the scan.
4. `probe_target(result: SearchResult) -> None` – run the existing probe and extraction routines on a result.  Providers should delegate to the shared probe functions rather than implementing their own.

Providers may also expose `get_default_options() -> ProviderOptions` to supply initial values for the options panel.

### ProviderOptions structure

Each provider will define its own `ProviderOptions` dataclass.  Shared fields like query string and result limit can be normalised, while provider‑specific fields (e.g., subreddit name, SearXNG instance URL, Censys API credentials) remain separate.  These options will be bound to the controls in the start‑scan dialog.

## 5. Integrate with existing probe and extract functions

The SearXNG and Reddit modules already support optional probe operations, but they currently call bespoke probe logic.  The new provider interface should call the central `probe_worker` functions used by Shodan scans.  This ensures consistent classification, indicator matching, and extraction across providers.  After a probe completes, update the `probe_status` and `probe_indicator_matches` fields in the `search_results` table.

## 6. Coordinate concurrent scanning

The new start‑scan dialog will allow users to select multiple providers at once.  A `ScanManager` class should orchestrate these concurrent scans:

* For each selected provider, call `start_scan` with its options and record the handle.
* Spawn tasks to consume results from each provider’s `yield_results` iterator.  As results arrive, insert them into the main DB and emit events to the live‑scan window.
* Provide a unified cancellation mechanism; when the user clicks “Stop”, call `stop_scan` on all handles and ensure threads or async tasks are cleaned up.

## 7. Testing and backward compatibility

* **Unit tests** should be added to verify that providers correctly implement the interface, that results are inserted into the `search_results` table, and that probe updates work as expected.
* **Migration tests** must ensure that importing from sidecar DBs produces the correct number of entries and preserves probe and classification data.
* **Deprecation plan:** after migration, mark experimental modules as deprecated but keep them accessible via the new Accessories menu.  This gives existing users time to adjust while preventing confusion for new users.

By following these steps, Dirracuda will gain a unified, extensible search framework that simplifies code maintenance and enhances user flexibility.