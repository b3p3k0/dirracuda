# Start Scan — Shodan provider field parity

**Status:** IMPLEMENTED on `development` (2026-10-06) — see
[SHODAN_SCAN_FIELDS_PLAN.md](SHODAN_SCAN_FIELDS_PLAN.md) §RA Closeout for the
commit map, security sign-off, and HI manual-test steps. This remains the HI
decision record; the open questions below were resolved in the plan (Q1
run-scoped via the existing gate; Q2 mirror Self-hosted reconcile; Q3
required-when-selected + a 500-char run-local cap). All code references here
were verified against the repo before implementation (running code outranks
this document). **Superseded by implementation:** Decision 1's "reveal toggle"
was removed during HI testing — it exposed the stored key without the Keymaster
passphrase. The shipped API-key field is a non-secret sentinel status
(`<CONFIGURED>`/`<not set>`) with an optional masked one-off override and no
reveal (see the plan's §RA Closeout, Card 6).

## Problem

The unified **Start Scan** dialog (`UnifiedScanDialog`) shows the **Self-hosted
Search** provider with an inline, editable `Instance` field and a single
editable `Query` field (+ `Dorkbook…`). The **Shodan** provider shows neither
its credential nor its query inline:

- The API key is set only via a launch-time prompt or the separate Keymaster
  window — never visible in the dialog.
- The per-protocol Shodan dorks are editable only through Dorkbook / App Config.

HI wants Shodan brought to parity with Self-hosted Search.

## Decisions (confirmed by HI)

1. **Shodan API-key row** — one shared, masked entry with a reveal toggle, and a
   `Keymaster…` button on the same line. One key per account, so this is a
   single field, not per-protocol.
2. **Per-protocol Shodan query grid (Option A)** — one editable query field per
   protocol (SMB / FTP / HTTP), aligned as `Max | Shodan query`, prefilled from
   each protocol's saved dork. Keep the **single** existing `Dorkbook…` button —
   not one per row.
3. **Grey out, don't hide** — the query + max cells for unchecked protocols stay
   in place but disabled (matches the panel's existing disable pattern; avoids a
   layout jump when protocols are toggled).
4. **Reach the backend run-scoped** — per-protocol queries are injected as
   *temporary* config overrides at launch via the existing
   `_temporary_config_override` plumbing. A one-off edit must **not** overwrite
   the saved dork.
5. **No CLI / workflow / schema change.** Downstream already reads these queries
   from config.

### Rejected alternative

- **Single shared query field (literal of the original ask).** Shodan discovery
  uses a *distinct filter per protocol*; one shared string finds hosts for only
  one protocol, so it weakens discovery. Rejected.
- **Persist edited queries to config on launch (Option "b").** Rejected: it
  mutates saved dorks as a side effect of a one-off edit. The run-scoped
  override is the correct, non-destructive path and matches how Self-hosted
  preserves run-local edits.

## Why per-protocol is correct (fact)

Shodan uses a different filter per protocol:

- SMB  → `smb authentication: disabled`
- FTP  → `port:21 "230 Login successful"`
- HTTP → `http.title:"Index of /"`

These run as separate Shodan searches with separate credit budgets. Self-hosted
can use one query because it is a web search classified into protocols *after*
the fact; Shodan cannot share one query across protocols.

## Proposed UI (Option A)

Full dialog in context:

```
┌─ Start Scan ──────────────────────────────────────────────────────────┐
│ Template  [ Select a template…          ▾ ]    [ Save ]   [ Delete ]    │
│                                                                         │
│ Providers                                  Queue: Shodan → Self-hosted  │
│ ─────────────────────────────────────────────────────────────────────  │
│ [✓] Shodan                                                [ Dorkbook… ] │
│     API key   [ ••••••••••••••••••••••• ]  [👁]  [ Keymaster… ]         │
│                                                                         │
│               Max     Shodan query  (one per selected protocol)         │
│     SMB  [✓]  [100]    [ smb authentication: disabled             ]     │
│     FTP  [✓]  [100]    [ port:21 "230 Login successful"           ]     │
│     HTTP [ ]  [100]    [ http.title:"Index of /"                  ]     │ ← greyed (HTTP off)
│     Est. cost: ~2 credits • Est. results: SMB ~100  FTP ~100     How?   │
│                                                                         │
│ [ ] Self-hosted Search   Instance […] Query […] [Dorkbook…] Results […] │
│ [ ] Reddit               Mode / Sort / Window / Posts / Query…          │
│ ─────────────────────────────────────────────────────────────────────  │
│ Targeting                          │ Runtime & safety                   │
│  Country [US       ] US, GB, CA    │  Concurrency [10]  Timeout [10] sec │
│  [ ]Africa [ ]Asia [ ]Europe …     │  [ ] Verbose   [ ] Bulk probe …     │
│  [Select all] [Clear]              │  SMB mode [Cautious ▾] …            │
│ ─────────────────────────────────────────────────────────────────────  │
│ Config: ~/.dirracuda/conf/config.json                   [ Edit Config ] │
│ Enter start • Esc cancel • Ctrl/Cmd+W close        [Cancel] [Start Scan] │
└─────────────────────────────────────────────────────────────────────────┘
```

Same panel, only SMB selected (shows the grey-out keeps it uncluttered):

```
[✓] Shodan                                                 [ Dorkbook… ]
    API key   [ ••••••••••••••••••••••••• ]  [👁]  [ Keymaster… ]
    SMB  [✓]  [100]   [ smb authentication: disabled              ]
    FTP  [ ]  [100]   [ port:21 "230 Login successful"            ]   (greyed, inert)
    HTTP [ ]  [100]   [ http.title:"Index of /"                   ]   (greyed, inert)
    Est. cost: ~1 credit • Est. results: SMB ~100                 How?
```

## Current behavior (facts — verify before trusting)

- **Dialog:** `gui/components/unified_scan_dialog.py` (controller),
  `gui/components/unified_scan_layout.py` (`_build_shodan_options`, layout),
  `gui/components/scan_provider_options.py` (sub-panel builders + persistence).
- **Shodan panel today:** protocol checkbox + per-protocol max-results entry ×3,
  one `Dorkbook…` button, and an estimate row (cost / results / How?). No key,
  no query field.
- **Shodan API key:** stored at config `shodan.api_key`. Read/persist helpers in
  `gui/components/dashboard_shodan.py`
  (`read_shodan_api_key_from_config` / `persist_shodan_api_key_to_config`). The
  launch gate `ensure_shodan_api_key_for_scan` sets
  `scan_options["api_key_override"]`. Keymaster `Apply` writes `shodan.api_key`
  into the active config. Entry point:
  `gui/components/keymaster_window.py::show_keymaster_window(parent, settings_manager=…, config_path=…)`.
- **Per-protocol Shodan dorks** — config paths, already read downstream:
  - SMB  → `shodan.query_components.base_query`
  - FTP  → `ftp.shodan.query_components.base_query`
  - HTTP → `http.shodan.query_components.base_query`
  - Contract + defaults: `shared/discovery_dork_config.py`
    (`DORK_CONFIG_PATHS`, `DORK_DEFAULTS`, `read_discovery_dorks`). The dialog
    reads them via `experimental/dorkbook/defaults.py::read_defaults`.
  - Consumed downstream by e.g. `commands/http/shodan_query.py`
    (`_resolve_http_base_query`) — so no change is needed there.
- **Run-scoped override mechanism:** `gui/utils/scan_manager.py` builds a
  `config_overrides` dict and runs the subprocess under
  `backend_interface._temporary_config_override(...)`
  (`gui/utils/backend_interface/interface.py:848`), which deep-merges overrides
  into a temp config, points the subprocess at it, then restores the path and
  deletes the temp file in `finally`. `api_key_override` and the per-protocol
  credit caps already ride this path.
- **Self-hosted reconcile pattern to mirror:** `gui/components/dorkbook_events.py`
  (`reconcile_query`, `bind_self_hosted_query`, `refresh_self_hosted_query`,
  `<<DorkbookApplied>>` + `FocusIn`). Dorkbook `Apply` broadcasts
  `(identity, destination, query)` where `destination` is `self_hosted` or
  `shodan:<proto>` — today only `self_hosted` is consumed by the dialog.

## Implementation leads for PA (verify, then turn into cards)

- **Dialog vars + grid:** add `shodan_smb_query_var` / `_ftp_` / `_http_`;
  render the aligned `Max | Shodan query` grid in `_build_shodan_options`; wire
  enable/disable to the protocol checkboxes alongside the existing max cells;
  prefill from `read_defaults`.
- **Key row:** masked `ttk.Entry` (`show="•"`) + reveal toggle + `Keymaster…`;
  refresh the field on `FocusIn` (Keymaster is non-blocking) so an applied key
  appears without reopening the dialog.
- **Scan request:** include each *selected* protocol's query; validate non-blank
  when its protocol is selected (mirror the self-hosted "query required" rule).
- **scan_manager:** add three `base_query` overrides into `config_overrides` in
  the per-protocol worker paths (SMB worker near line 352; FTP builder near
  ~1013; HTTP builder near ~1167 — **confirm exact lines**). Mirror the
  `api_key_override` block.
- **Shodan query refresh:** extend the applied-dork binding to react to
  `shodan:<proto>` destinations (today only `self_hosted` is handled).
- **Tests:** `gui/tests/test_unified_scan_dialog*.py` (layout/validation) plus
  scan_manager override coverage. Keep Shodan mocked — never hit the network.
- **File-length guard:** `unified_scan_dialog.py` is ~1,163 lines; the
  1,700-line production-code limit applies. Put new builders in
  `scan_provider_options.py` / `unified_scan_layout.py`.

## Open questions for PA to resolve (recommend with reasoning)

- **Key persistence semantics:** does a typed inline key persist to
  `shodan.api_key` on launch (continuous with today's prompt) or stay run-scoped
  via `api_key_override` only? Lean: persist-on-launch to match current
  behavior; confirm.
- **Query prefill vs run-local edits:** confirm the reconcile model so an edited
  row survives a Dorkbook refresh but a Dorkbook `Apply` replaces it (mirror
  Self-hosted).
- **Validation / max length:** parity with Self-hosted (≤500 chars there);
  decide Shodan's rule.

## Out of scope

- Self-hosted Search and Reddit panels — unchanged.
- No CLI / workflow / schema changes.
- No change to the preflight credit estimate.

## References

- Agent Charter: https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AGENT_CHARTER.md
- Development SOP: https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/DEVELOPMENT_SOP.md
- Repository instructions: ../../../AGENTS.md (GUI→CLI subprocess boundary;
  `gui.utils.safe_messagebox` only; named theme styles only; `ensure_dialog_focus`
  on `grab_set` dialogs; 1,700-line production-code limit; `./dirracuda` is the
  only runtime entrypoint).
- Related lessons: `../scan_dialogs/`, `../promote_reddit_and_websearch/`,
  `../kbd_ctrl_improve/`.
