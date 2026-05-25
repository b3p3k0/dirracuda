# Towards a Standard Integration Language

One of Dirracuda’s main objectives for this overhaul is to define a consistent “language” that search modules can use to communicate with the rest of the system.  This language encompasses the data structures, method signatures and event semantics through which providers deliver results, request probes, and persist data.  A clear contract reduces coupling between modules and makes it easier to add new providers or features in the future.

## 1. Core concepts

* **Provider:** A module that queries an external search service (Shodan, SearXNG, Reddit, Censys, etc.), handles authentication and paging, and emits `SearchResult` objects.
* **SearchResult:** A normalised representation of a finding.  It stores the raw target (URL, host, etc.), derived attributes (protocol, port), provider metadata (score, snippet), and status fields such as `probe_status` and `classification`.  See the provider integration document for field details.
* **Probe:** A process that performs an active connection to a target to identify services, gather banners or run extraction logic.  The probe updates the `SearchResult` with fields like `probe_indicator_matches` and `classification` and may generate additional events.
* **Live‑scan window:** A UI component that displays results as they arrive.  It subscribes to events emitted by providers and the `ScanManager` and renders results consistently regardless of provider origin.

## 2. Event‑driven architecture

To decouple providers, probe workers and the UI, adopt an event‑driven pattern:

* **Events:** Use a set of well‑defined events such as `ScanStarted`, `ScanProgress(provider, count)`, `ResultFound(SearchResult)`, `ProbeCompleted(SearchResult)`, `ScanCompleted`, and `ScanCancelled`.  Each provider will emit these events as appropriate.
* **Dispatcher:** Implement a central event dispatcher (e.g., using an observer pattern or a lightweight message bus) that allows components to subscribe to specific event types.  The live‑scan window, logging facilities, and database layer can register handlers to process events.
* **Backpressure and flow control:** For high‑volume providers, allow the dispatcher to apply backpressure or buffering.  Results should not overwhelm the UI or DB; therefore, consider batching updates or throttling probe requests.

## 3. Provider API contract

All providers must adhere to the following contract (pseudo‑code):

```python
class Provider(Protocol):
    def start_scan(self, options: ProviderOptions) -> ProviderHandle:
        """Start a scan with the given options and return a handle for control."""

    def yield_results(self, handle: ProviderHandle) -> Iterator[SearchResult]:
        """Yield search results as they are discovered.  Must not block for long periods."""

    def stop_scan(self, handle: ProviderHandle) -> None:
        """Stop the scan gracefully.  Must be safe to call multiple times."""

    def probe_target(self, result: SearchResult) -> SearchResult:
        """Perform an active probe on the result and return an updated SearchResult."""

    def get_default_options(self) -> ProviderOptions:
        """Return a dataclass populated with provider‑specific default settings."""

class ProviderOptions(Protocol):
    # Provider‑specific fields (query, credentials, limits, etc.)
    pass

class ProviderHandle: ...  # Implementation‑specific handle used by the scan manager
```

Providers may implement asynchronous versions of these methods if using `asyncio`.  However, the interface should remain consistent from the perspective of the `ScanManager`.

## 4. Database integration and persistence

* **Single table schema:** The `search_results` table will store all results across providers.  Fields must be flexible enough to accommodate provider‑specific metadata.  Consider using a JSON column to hold provider‑specific details when necessary.
* **Atomic writes:** Providers should write results to the DB via a shared data access layer.  This layer will handle upserts, deduplication, and referential integrity (e.g., linking to a `scans` table).  Writing directly from UI threads should be avoided.
* **Migration paths:** Use versioned database schemas and migrations to evolve the storage format without breaking existing installations.

## 5. Data flow example

1. User selects providers and options in the start‑scan dialog and clicks **Start**.
2. The `ScanManager` creates a handle for each provider by calling `start_scan`.  It then enters an event loop where it repeatedly calls `yield_results` on each provider.
3. When a `SearchResult` is yielded, the `ScanManager` dispatches a `ResultFound` event containing the result.  The DB layer persists the result, the live‑scan window adds a row, and the provider may optionally schedule a probe.
4. If probes are enabled, the `ScanManager` calls `probe_target` and dispatches `ProbeCompleted` when done.
5. Once all providers indicate completion, `ScanCompleted` is dispatched.  If the user clicks **Stop**, `stop_scan` is called on each provider and `ScanCancelled` is dispatched.

## 6. Future extensions

* **Classification plugins:** Define a `ClassificationPlugin` interface that can analyse a `SearchResult` (or probe output) and assign labels, confidence scores, or tags.  This allows for modular classification algorithms.
* **Export pipeline:** Standardise an export interface so that results can be sent to external systems (e.g., CSV, JSON, Splunk) via the same event mechanism.

Establishing this standard integration language will ensure that Dirracuda’s growing set of search providers, probes, classifiers and UIs remain loosely coupled and extensible.  It codifies the implicit conventions currently scattered across experimental modules and paves the way for reliable integrations.