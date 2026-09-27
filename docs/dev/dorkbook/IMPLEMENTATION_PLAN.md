# Unified Dorkbook and shipped dorks

Date: 2026-09-27
Status: HI approved execution. U1 implemented; validation recorded in
[U1_VALIDATION.md](U1_VALIDATION.md). U2–U6 remain pending.

## Outcome

One Dorkbook library for Shodan and Self-hosted Search, followed by a broader
shipped collection that gives newcomers useful starting points. Self-hosted
Search is a core provider; leftover experimental presentation is retired where
this work touches it. Keep compatible internal paths and provider IDs.

## Agreed product behavior

- One view with expanded Shodan and Self-hosted Search groups and dorks beneath
  them. No provider/protocol tabs. Groups can collapse.
- Show name, applicable protocol, topic, query preview, and saved-default mark.
  Shodan rows identify SMB, FTP, or HTTP. Web-search rows have no protocol choice.
- Search names, queries, and notes across both groups; combine with a topic filter.
- Selection shows full query, notes, built-in/custom status, and destination in
  a preview panel. Selection and double-click do not persist a change.
- **Apply to Search** saves the query as the destination's default across runs
  and restarts. There are four independent destinations: Shodan SMB/FTP/HTTP
  and Self-hosted Search.
- Update the matching open scan input after a successful apply. Never start a
  scan, enable another provider, change unrelated options, or mutate a run
  already in flight. Show success only after persistence succeeds.
- Derive default marks from saved query text, not a stored active-dork pointer.
  An ad-hoc query can match no dork. Editing/deleting a custom dork does not
  silently rewrite its previously applied query; applying is explicit.
- Built-ins stay read-only and italic. Preserve custom add/edit/delete, copy,
  delete confirmation/session mute, singleton behavior, and window geometry.
- Accessories and scan-query controls open the same library. A contextual
  launch focuses the appropriate provider without hiding the other group.
- Keep direct query entry in scan controls. Dorkbook owns saved dorks; scan
  controls own run setup. There is no second provider-specific dork collection.
- Desktop and Web UI share provider-aware storage and persistent application.
  Web UI adopts the grouping and wording while retaining its existing CRUD
  scope; full desktop/Web CRUD parity is not required by this card.
- Use **dork** for a saved item and **query** for its text. Labels include
  Find Dork, Add Dork, Selected Dork, Copy Query, and Apply to Search.

Layout: [current ASCII mockup](ASCII_SKETCHES.md#unified-library--review-draft-2026-09-27).

## Data and settings contract

Keep the existing Dorkbook sidecar and canonical path service. Add provider and
topic metadata; model protocol independently from provider. Shodan requires one
of SMB/FTP/HTTP. Self-hosted Search has a single destination without a fabricated
WEB protocol. Topics describe content and do not affect query execution.

Existing rows become Shodan dorks with a neutral topic, preserving IDs, keys,
queries, notes, row kinds, and timestamps. Duplicate identity is provider,
applicable protocol, and trimmed exact query; explicitly handle the no-protocol
case so SQLite NULL uniqueness does not allow duplicates. Keep the current
policy of preserving custom rows on built-in seed/refresh collisions.

Use one shared apply/read-default implementation for both UIs. Retain Shodan's
existing config keys. Give Self-hosted Search one authoritative persisted default
through existing configuration services, with an explicit fallback/import rule
for its saved desktop query. Existing nonempty settings must not be overwritten
by installation of new built-ins. Browser preferences and stale open forms must
not silently take precedence over an applied default. Preserve intentional
manual input and existing unrelated preferences.

Migration must inspect actual columns, constraints, and indexes, handle fresh
and legacy sidecars, be transactional and repeatable, and preserve the previous
database on failure. Take a SQLite-consistent backup before changing an existing
sidecar. Reject unsupported schemas with an actionable message. Verify row
preservation and constraints before committing. Document restore/downgrade
limits; do not assume older binaries understand the new schema.

The main results DB schema is outside scope. Keep old Shodan API callers working
when provider is omitted; new callers identify provider explicitly. Preserve
authentication, CSRF, and same-origin checks on existing Web UI operations.

## Cards, in order

| Card | Deliverable | Gate |
|---|---|---|
| U1: Shared storage | Provider/topic model, legacy sidecar migration, seed/CRUD compatibility | Fresh/legacy/idempotent migration, failure rollback, duplicate and collision tests |
| U2: Saved defaults | Shared read/apply service, legacy Self-hosted Search preference handling, stale-input policy | Four independent destinations, save failures, restart and manual-input behavior |
| U3: Desktop | Grouped Dorkbook, preview, filters, default marks, contextual launch and scan-field refresh | Mocked UI/wiring checks plus HI layout and restart check |
| U4: Web UI | Same catalog and application contract, grouped presentation, contextual access | Route/default behavior and existing auth/CSRF/origin regression checks plus HI browser check |
| U5: Shipped dorks | Broad researched candidates, HI pruning, provider-specific built-ins and useful notes | Catalog invariants and upgrade/collision tests; live yield remains a separate HI check |
| U6: Closeout | Terminology/docs cleanup, removal of superseded dead paths, final evidence and lessons | Focused regression, docs review, file-size report, HI acceptance |

Work one card at a time: confirm, state root cause, fix, validate, report, wait.
Update docs with each card rather than leaving all documentation to U6. Commit
permission is available for this task; suggest checkpointing completed work
before changing cards. No push. If delegation helps, the lead acts as PA/RA and
assigns a bounded card or independent subtask to a DA, then reviews its diff and
validation. Shared contracts precede dependent UI work.

## Shipped collection

Selection menu: [numbered candidate dorks](CANDIDATE_DORKS.md).

Retain the three original broad dorks, keys, and query text. Add a broad
Self-hosted Search option, but do not silently activate it over existing input.
HI selected candidate IDs **1–10, 12–14, 19–22, 26–29, 32**, plus **33A and 33B**.
Caddy/custom listing exploration (33C) is deferred. IDs represent ideas,
not a final count of provider/format variants. HTTP/web search will likely have
more useful content-specific options than SMB/FTP; do not force equal counts.

The collection should spark imagination and teach adaptation. Best-effort
content matching is sufficient; imperfect results are part of exploration.
Prefer clear examples users can change over excessively tuned queries. Syntax
and runtime compatibility still matter; measured live yield is not a ship gate.

Each dork gets a readable name, topic, correct provider syntax, a short account
of what it matches, and material limitations. Record source and evidence in
workspace research notes. Distinguish documented syntax from measured yield.
Do not auto-translate queries or claim every upstream search engine supports
the same operators. Candidate count is not verified-directory count.

Calibre/Calibre-Web/OPDS application support and Reddit saved searches are
deferred in this proposed scope. Generic ebook directory dorks are included.
No changes to directory verification, crawling, downloading, result retention,
provider scheduling, or the GUI-to-CLI subprocess boundary.

## Validation and operating limits

Existing baseline (before implementation): 48 Dorkbook store/desktop/Web tests
passed, with one dependency deprecation warning. That does not validate new work.

Starting focused suite, extended with each card's new tests:

```bash
./venv/bin/python -m pytest \
  shared/tests/test_dorkbook_store.py \
  gui/tests/test_dorkbook_window.py \
  gui/tests/test_discovery_dork_config.py \
  gui/tests/test_scan_dork_editor_dialog.py \
  gui/tests/test_unified_scan_dialog_searxng_controls.py \
  experimental/webui/tests/test_dorkbook_routes.py \
  experimental/webui/tests/test_searxng_routes.py -q
```

Add configuration-store and scan-provider checks when those components change.
Use temporary databases and mocked network calls. Compile touched Python files
and run `git diff --check`. Broaden regression only where integration risk warrants.
Do not query paid APIs or live target hosts as part of automated tests.

### Authorized live backend checks (U4/U5)

HI owns both SearXNG and DeGoog instances and explicitly authorizes live query
testing, including repeated requests that may encounter upstream throttling.
This is a separate, opt-in validation lane, never a pytest fixture.

1. HI supplied both backend URLs. Metadata reachability passed for both during
   U1: DeGoog `/api/search-tabs` and SearXNG `/config` returned HTTP 200 with the
   expected JSON fields. Keep private endpoint URLs out of published docs.
2. Query SearXNG `/config`, then `/search` with `format=json`; query DeGoog
   `/api/search-tabs`, then `/api/search` with `type=web`. Confirm production
   backend detection and the response shape for actual selected dorks.
3. Run the broad default, 33A, 33B, and representative book/video/music/photo
   dorks on each backend; then expand across the approved catalog as useful.
   Record query, backend, returned count, engine errors, elapsed time, and date.
4. Exercise page 2 where available and observe empty results and rate limits.
   Respect `Retry-After` and existing backoff; record 429/CAPTCHA/upstream errors
   as environmental limits, not reasons to discard a useful learning dork.
5. Confirm an applied default reaches the matching search request after UI
   reopen/restart. Live query checks use the owned aggregators; storage and
   returned-page classification use disposable DBs/fixtures, not the user's
   primary DB. No downloads or bulk probing are part of these checks.
6. Existing `scripts/live_test_searxng.py` is an optional separate end-to-end
   harness; audit its current compatibility before reuse because it also
   verifies returned target URLs. An API-only check is sufficient for catalog
   syntax and live backend behavior. Record exact executed commands and results.

Live checks are required by this plan, but high yield is not a shipping gate.
If an endpoint is unavailable, report the exact failing request and required
unblock step; keep its live status PENDING rather than treating mocks as proof.

HI acceptance: launch `./dirracuda`; check the grouped view, filtering, built-in
protection and custom CRUD; apply one dork per destination; check open scan
fields; restart and confirm defaults; repeat application through Web UI and
check desktop/browser consistency. Test catalog yield separately if desired.

Record exact commands and PASS/FAIL; manual status stays PENDING until HI checks.
Check touched-file sizes before/after against the agreed rubric and pause above
1,700 lines. Current pressure points: Web UI `app.py` 1,501 lines; Technical
Reference 1,654. Prefer small modules and concise cross-links over large additions.

Review README and Technical Reference against code; correct the existing false
claim that Dorkbook application leaves an unsaved edit. Update workspace spec,
roadmap/cards, mockup, open questions, validation, and lessons. Clearly label
historical material. Remove obsolete code only after checking live references
and compatibility callers; retain necessary import shims and API contracts.

## Research references

- [Shodan syntax](https://help.shodan.io/the-basics/search-query-fundamentals)
- [SearXNG query behavior](https://docs.searxng.org/dev/search_api.html)
- [DeGoog API](https://degoog-org.github.io/docs/api.html)
- [SQLite supported schema-change procedure](https://www.sqlite.org/lang_altertable.html)

This plan includes a Dorkbook-sidecar migration. Approval covers this scoped
design; unrelated protected auth/dependency/CI or main-DB changes remain outside it.
