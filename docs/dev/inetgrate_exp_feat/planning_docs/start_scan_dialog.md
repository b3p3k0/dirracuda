# New Start‑Scan Dialog Design

The existing “Start Scan” functionality is Shodan‑centric and does not allow users to combine multiple search providers.  This document proposes a revised dialog that presents all available providers in one place, allows provider‑specific configuration, and streams results into the live‑scan window.

## 1. High‑level behaviour

1. **Invocation:** When the user clicks the “Start Scan” button on the dashboard (or presses its keyboard shortcut), a modal dialog appears.  This dialog should be styled consistently with other Dirracuda dialogs (simple header, clear typography, minimal padding), avoiding the extra padding and unusual fonts used in the current Shodan dialog.
2. **Provider selection:** The dialog displays an alphabetical list of available providers (e.g., Censys, Reddit, SearXNG, Shodan).  Each provider is presented with a checkbox to include or exclude it from the scan.  Providers are enabled only when all required credentials are configured; if credentials are missing, the row should indicate that configuration is needed and provide a quick link to the provider’s options panel.
3. **Options panels:** Selecting a provider reveals its options panel.  Options panels are collapsible sections that appear below the provider list or next to it in a two‑column layout.  Within each panel, input fields control query strings, result limits, API keys, and any provider‑specific settings (e.g., SearXNG instance URL, Reddit mode/search term, Censys filter).  The fields should use familiar control types (text entries, comboboxes, checkboxes) and conform to the general UI style used in other configuration dialogs.
4. **Launch and cancellation:** At the bottom of the dialog, two buttons are shown: **Start** and **Cancel**.  Clicking **Start** will collect options for all selected providers and invoke the `ScanManager` to begin scanning (see provider integration doc).  A progress bar and status messages should appear within the live‑scan window rather than in the dialog itself.  Clicking **Cancel** simply closes the dialog.
5. **Validation:** The dialog should prevent starting a scan unless at least one provider is selected and all required fields for selected providers are filled.  Display clear error messages near the offending fields and disable the **Start** button until the issues are resolved.

## 2. Suggested layout

The following layout ensures clarity while keeping the dialog compact:

| Component | Description |
| --- | --- |
| **Provider list panel** | A scrollable list of providers with checkboxes.  Providers are sorted alphabetically.  Each row shows the provider name, a short description/tool tip, and an indicator when configuration is missing. |
| **Options panel stack** | Below the list (on smaller screens) or in a second column (on larger screens), a vertical stack of collapsible panels appears.  Each panel header shows the provider name with a disclosure triangle; the body contains input widgets for that provider’s options.  Panels are created only when their provider is selected to avoid clutter. |
| **Action buttons** | A horizontal bar at the bottom with **Start** (primary) and **Cancel** (secondary) buttons.  The **Start** button is disabled until validation passes. |

Avoid long sentences in table cells: lists or phrases should be separated with semicolons or bullet points.

## 3. Provider‑specific options examples

* **Shodan:** API key (if not globally configured), query string (to override the default query), protocol filters (SMB/FTP/HTTP), result limit, region/country filters, concurrency, timeouts, and verbosity toggles.  Move existing Shodan configuration fields out of the global settings panel and into this section to maintain locality.
* **SearXNG:** Instance URL (e.g., `https://<your-instance>/`), query string, maximum results to fetch, optional “bulk probe results” toggle, and classification options.  These options mirror the fields currently provided in the SearXNG experimental tab【572388059610040†L86-L175】, but should adopt the unified styling.
* **Reddit:** Mode (feed/search/user), subreddit or username, search query (if mode = search), maximum posts, parse body flag, include NSFW, replace cache, and “probe results” toggle.  These correspond to fields in the existing Reddit ingestion dialog【685657181541025†L96-L180】.
* **Censys:** API ID and secret, query string, result limit, and optional filters.  Provide a “test credentials” button to validate login.

## 4. Integration with live‑scan window

* The dialog should not display real‑time output.  Instead, once a scan begins, each provider will stream results to the live‑scan window.  For providers that currently do not support live streaming (e.g., SearXNG and Reddit), a placeholder should appear in the live‑scan output indicating progress and approximate counts.  The provider integration plan includes updating these modules to emit events as results arrive.
* The live‑scan window should tag results with their provider name and use consistent colours or icons to help users differentiate sources.

## 5. Backward compatibility

Until all providers are migrated to the new dialog, the old Shodan quick scan dialog should remain accessible via a “Classic Shodan Scan” button within the new dialog or via a command‑line option.  Once migration is complete and user feedback is positive, the old dialog can be removed entirely.

## 6. Implementation notes

* Reuse the `ScanDialog` and template system where possible to create a consistent look.  However, avoid the heavy nested frames used in the current Shodan dialog; adopt a flatter hierarchy for easier maintenance.
* Use the common provider interface described in the provider integration plan to dispatch scans.  The options collected here should be passed as `ProviderOptions` objects.
* If multiple providers are selected, launch their scans concurrently and merge results in the live‑scan window.  Provide clear cancellation semantics.

By implementing this dialog design, Dirracuda will offer a unified and intuitive way for users to configure and launch multi‑provider scans while preserving the flexibility of provider‑specific options.