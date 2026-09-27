# Dorkbook across discovery providers

Date: 2026-09-27
Status: Provider grouping, persistent application, and terminology agreed.
The [implementation plan](IMPLEMENTATION_PLAN.md) is implemented through U6.
This document preserves the original design rationale; see [validation](UNIFIED_VALIDATION.md).

## Why this comes before the dork pack

The requested starter pack covers ebooks, movies/TV, music, photos, and other
collections. Research exposed a product boundary: Dorkbook originally applied
Shodan queries, while Self-hosted Search accepts web-search queries. HI asked
to explore consolidating both in Dorkbook before choosing the shipped dorks.
The broader starter pack remains the original objective.

## Behavior before consolidation (historical)

- Dorkbook has SMB/FTP/HTTP tabs and one built-in per protocol.
- Its sidecar schema restricts protocol to those three values. There is no
  provider field; duplicate queries are scoped by protocol.
- Desktop and Web UI apply a selected dork immediately to Shodan discovery
  config. The main README incorrectly describes an unsaved editor change.
- Desktop Self-hosted Search keeps its query in Start Scan preferences under
  `unified_scan_dialog.searxng_query`.
- Self-hosted Search supports SearXNG and DeGoog. Both feed the existing HTTP
  classification/retention flow; new results go to the primary database.
- Dorkbook remains a dork sidecar. Consolidating dorks does not require
  moving them into the primary results database.
- Typical Calibre application pages do not meet the current HTTP verifier's
  directory-title requirement. A shared library alone does not add Calibre support.

## Proposed experience

Keep Dorkbook as the one shared dork library, reachable from Accessories and
from query controls in Start Scan. These are entry points to the same library,
not separate collections. Existing scan fields remain available for query input.

HI selected one view with provider headings and dorks beneath each heading;
no provider or protocol tabs. Shodan and Self-hosted Search start expanded.
Protocol appears as a column on Shodan rows; topic and text search work across
both groups. Opening from a scan query scrolls to the relevant provider without
hiding the other provider. See the current draft in `ASCII_SKETCHES.md`.

A dork records its provider, applicable protocol, topic, name, query, and
notes. Provider and protocol are separate concepts; web search must not become
a fake fourth protocol. Store provider-specific variants as separate dorks.
Do not attempt automatic translation between Shodan and web-search syntax.

SearXNG and DeGoog share the Self-hosted Search collection where syntax permits.
Notes identify engine-specific assumptions. An aggregator does not guarantee
that every upstream engine interprets an operator the same way.

HI selected the label **Apply to Search** and persistence across runs.
Applying saves the query as the default for its destination: Shodan SMB, FTP,
HTTP, or Self-hosted Search. Defaults survive restart. It also updates a matching
open scan field so a stale dialog cannot silently restore the previous value.
It does not start a scan, enable a provider, change unrelated settings, or alter
an already-running scan. Report success only after the save succeeds.

Proposed interaction: selecting a row previews it; saving requires the explicit
Apply to Search button. The preview names the destination and persistence effect.
Each destination's saved query is marked in the list when it matches a dork;
an ad-hoc saved query may match none. Built-in status and saved-default status
are separate. No new stored active-dork pointer is implied by this mockup.

## Compatibility and implementation gates

### Language pass (HI requested)

Use **dork** for a saved library item and **query** for its search text. Labels
include Find Dork, Add Dork, Selected Dork, Copy Query, and Apply to Search.
Avoid the former synonym in current user-facing text. Apply this consistently
to desktop and Web UI headings, actions, counts, empty states, confirmations,
help text, README, Technical Reference, and current workspace docs. Update
nearby docstrings and wording-sensitive tests with the corresponding UI work.
Historical v1 sketches remain labelled as history until superseded.

This is a wording pass, not a rename of database tables, API fields, or stored
settings. Existing `entry_id`, `query`, and other compatibility contracts remain.

### Data and runtime

- Existing rows must retain IDs, stable built-in keys, queries, and custom notes;
  classify them as Shodan dorks during any migration.
- Duplicate scope must distinguish providers. Preserve the current custom-row
  precedence when refreshing built-ins.
- Inspect real sidecar columns, constraints, and indexes before deciding the
  migration. A protocol-only schema cannot safely represent both query families.
- Keep existing callers/API behavior compatible; desktop and Web UI must agree
  on dork identity and application semantics.
- Keep dork browsing local; no search requests or credit use while browsing.
- `experimental/webui/app.py` is 1,501 lines. Keep route additions small or
  extract Dorkbook routes; pause for a modularization plan above 1,700 lines.
- Keep internal `experimental/`, `se_dork`, and `searxng` names where needed for
  compatibility; mainstream status does not require moving implementation files.

Suggested cards, one at a time: review the single-view mockup; implement
the shared library with compatibility validation; curate and ship the larger
starter pack. Calibre application support remains a separate candidate card.

## Remaining scope and review

1. Grouped view and preview are accepted in the implementation plan.
2. Reddit search dorks are deferred; this pass covers Shodan and Self-hosted Search.
3. HI authorizes PA/RA-led delegation to developing agents when useful. No
   delegation is needed for this mockup; split bounded implementation cards
   only after the design and compatibility plan are concrete.

## References and limits

- [Shodan query fundamentals](https://help.shodan.io/the-basics/search-query-fundamentals)
- [SearXNG query API and upstream syntax caveat](https://docs.searxng.org/dev/search_api.html)
- [DeGoog search API](https://degoog-org.github.io/docs/api.html)
- [Current Self-hosted Search contract](../searxng_dork_module/SPEC.md)

Research verifies interface/syntax contracts, not dork yield. HI authorized live
checks against both owned aggregators; see the plan's separate validation lane.
