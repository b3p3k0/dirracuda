# Start Scan — Shodan provider field parity — Implementation Plan

**Role:** PA (Planning). **Track:** Full process (credential surface + multiple
modules). **Status:** IMPLEMENTED (incl. Card 6 redesign) — HI approved; built,
reviewed (the credential surface passed two independent security reviews, incl.
the Card 6 fix for a reveal bypass HI found in testing), and committed on
`development` (not pushed). Ready for HI re-test. See §RA Closeout at the bottom.
Original planning note follows.

Approval gated implementation, not the PA write. DA implemented one card at a
time; RA (a different session than each DA) reviewed and owned the acceptance
commits. Running code outranks any doc — line numbers below were verified
against the repo on 2026-10-06 (see §Grounding).

Authoritative intent: [SHODAN_SCAN_FIELDS.md](SHODAN_SCAN_FIELDS.md) (HI decision
record). This plan turns it into task cards and resolves its three open questions.

---

## Context

The unified **Start Scan** dialog shows **Self-hosted Search** with an inline,
editable query (+ `Dorkbook…`) and an instance field. **Shodan** shows neither
its credential nor its per-protocol dorks inline — the API key is only reachable
via a launch-time prompt or the separate Keymaster window, and the per-protocol
dorks only via Dorkbook/App Config. HI wants Shodan brought to parity: a shared
masked API-key row with a Keymaster button, and a per-protocol editable Shodan
query grid (Option A). Edits must reach the backend **run-scoped** (never
overwriting saved config), via the existing `_temporary_config_override`
plumbing. No CLI/workflow/schema change.

---

## Goal

Shodan sub-panel reaches field parity with Self-hosted Search:

1. One shared, masked API-key row: `Entry(show="•")` + reveal toggle +
   `Keymaster…` button.
2. Per-protocol editable Shodan query grid (SMB/FTP/HTTP), aligned `Max | Query`,
   prefilled from each protocol's saved dork, with a single `Dorkbook…` button.
3. Unchecked protocols grey out (not hidden).
4. Per-protocol queries and the inline key reach the backend **run-scoped** via
   `_temporary_config_override` / `api_key_override` — one-off edits never
   overwrite saved config.

## Non-goals

- Self-hosted Search and Reddit panels — unchanged.
- No CLI / workflow / schema change; no change to downstream query resolvers.
- No change to the preflight credit estimate logic (the estimate row only moves
  position within the restructured panel).
- No new modal dialog / no new `grab_set` (see §Elevated-risk).

---

## Current state (verified file paths + lines)

| Concern | Location (verified) |
|---|---|
| Dialog controller | [unified_scan_dialog.py](../../../gui/components/unified_scan_dialog.py) — 1163 lines |
| Shodan panel builder (inline today) | [unified_scan_layout.py:359-406](../../../gui/components/unified_scan_layout.py#L359-L406) `_build_shodan_options` — 656-line file |
| Provider sub-panel builders + persistence | [scan_provider_options.py](../../../gui/components/scan_provider_options.py) — 634 lines; has `build_searxng_sub_panel` (102), `build_reddit_sub_panel` (222); **no** `build_shodan_sub_panel` yet |
| Self-hosted reconcile | [dorkbook_events.py](../../../gui/components/dorkbook_events.py) — 62 lines; `refresh_self_hosted_query` (26), `bind_self_hosted_query` (43), `broadcast_applied` (13); `reconcile_query` in [defaults.py:120-122](../../../experimental/dorkbook/defaults.py#L120-L122) |
| Reconcile bind hook (call site to mirror) | [unified_scan_dialog.py:141-144](../../../gui/components/unified_scan_dialog.py#L141-L144) |
| Dorkbook Apply broadcast | [dorkbook_window.py:46](../../../gui/components/dorkbook_window.py#L46) `_destination` → `"self_hosted"` or `"shodan:"+protocol`; emitted at line 466 |
| Per-protocol dork prefill source | [defaults.py:46,66](../../../experimental/dorkbook/defaults.py#L46) `read_defaults()` → keys `"shodan:SMB"/"shodan:FTP"/"shodan:HTTP"` + `"self_hosted"` (protocol is **uppercase**) |
| Dork config paths + defaults | [discovery_dork_config.py:13-31](../../../shared/discovery_dork_config.py#L13-L31) `DORK_DEFAULTS`, `DORK_CONFIG_PATHS` (tuple segments) |
| Key store at `shodan.api_key` | [config.py:471-476](../../../shared/config.py#L471-L476) `get_shodan_api_key()` |
| Key read/persist helpers (`dash`-scoped) | [dashboard_shodan.py:204,231](../../../gui/components/dashboard_shodan.py#L204) `read_/persist_shodan_api_key_to_config` |
| Launch gate | [dashboard_shodan.py:86](../../../gui/components/dashboard_shodan.py#L86) → delegates to [dashboard_scan.py:556-594](../../../gui/components/dashboard_scan.py#L556-L594) `ensure_shodan_api_key_for_scan` |
| Keymaster entry (modeless) | [keymaster_window.py:1469-1475](../../../gui/components/keymaster_window.py#L1469-L1475) `show_keymaster_window(parent, *, settings_manager=None, db_path=None, config_path=None)` |
| Run-scoped override ctx mgr | [interface.py:848](../../../gui/utils/backend_interface/interface.py#L848) `_temporary_config_override(overrides)` — deep-merges **nested dicts**, temp file, restores + deletes in `finally` |
| Per-protocol override workers | [scan_manager.py:352](../../../gui/utils/scan_manager.py#L352) SMB (`_scan_worker`), [1013](../../../gui/utils/scan_manager.py#L1013) FTP (`_ftp_scan_worker`), [1167](../../../gui/utils/scan_manager.py#L1167) HTTP (`_http_scan_worker`) — 1274-line file |
| Downstream query readers (no change) | SMB [discover/shodan_query.py:217-226](../../../commands/discover/shodan_query.py#L217-L226); FTP [ftp/shodan_query.py:197-211](../../../commands/ftp/shodan_query.py#L197-L211); HTTP [http/shodan_query.py:228-233](../../../commands/http/shodan_query.py#L228-L233) `_resolve_http_base_query` |

**Config key paths the overrides must set** (nested dicts — the ctx mgr does
**not** split dotted strings):

- SMB → `{"shodan": {"query_components": {"base_query": q}}}`
- FTP → `{"ftp": {"shodan": {"query_components": {"base_query": q}}}}`
- HTTP → `{"http": {"shodan": {"query_components": {"base_query": q}}}}`

The correct template to copy is the **`max_results`** block (same protocol
scoping), **not** the `api_key` block (always global) and **not** the
credit-budget block (always global). E.g. FTP max_results at
[scan_manager.py:1021-1027](../../../gui/utils/scan_manager.py#L1021-L1027) —
mirror it, swapping `query_limits`→`query_components`, `max_results`→`base_query`.

---

## Open questions — resolved (recommendation + reasoning)

### Q1 — API-key persistence semantics → **Run-scoped via `api_key_override`; reuse the existing gate for persistence. Do not add new persistence code.**

The dialog's only new responsibility is to populate
`scan_options["api_key_override"]` from the inline field when non-blank. The
existing gate [dashboard_scan.py:556-594](../../../gui/components/dashboard_scan.py#L556-L594)
then yields exactly the behavior HI wants, with no change to it:

- **No key saved yet** + inline value present → gate skips the prompt, **persists
  to `shodan.api_key`**, and sets the run-scoped override. (Continuous with
  today's prompt-then-save.)
- **Key already saved** → gate early-returns **without** persisting and **without
  clearing** our override, so an inline edit flows to scan_manager run-scoped and
  the saved key is **not** overwritten.

This mirrors the query decision (one-off edit ≠ overwrite saved), keeps
Keymaster/App Config as the durable source of truth, and — importantly for an
Elevated-Risk surface — writes **no new secret-handling code**: it reuses the
audited helper. (Rejected: always-persist-on-launch — silently clobbers the
stored key on a typo and competes with Keymaster.)

### Q2 — Query prefill vs run-local edits → **Mirror Self-hosted exactly.**

Prefill each protocol query from `read_defaults(path)["shodan:<PROTO>"]`
(uppercase keys), keep a per-protocol baseline, and reconcile with the shared
`reconcile_query(current, baseline, latest)` (`return latest if current ==
baseline else current`). Net: a run-local edit **survives** a FocusIn/Dorkbook
refresh; a Dorkbook **Apply** for `shodan:<PROTO>` **replaces** that row and
resets its baseline. This extends `dorkbook_events` to consume the
`shodan:<PROTO>` destinations that Dorkbook already broadcasts but the dialog
ignores today.

### Q3 — Validation / max length → **Required-when-selected (new), plus a 500-char run-local cap (new safety rail).**

- **Required when selected:** reject a blank Shodan query for any *selected*
  protocol, mirroring the Self-hosted "query required" rule at
  [unified_scan_dialog.py:976-979](../../../gui/components/unified_scan_dialog.py#L976-L979).
  Do **not** validate unchecked/greyed protocols (inert, not submitted).
- **Max length:** cap each submitted Shodan query at **500 chars**, matching the
  figure HI referenced. Note this is **new**, not a mirror: Self-hosted's 500-cap
  lives only in Dorkbook's `apply_default` persistence
  ([defaults.py:96-97](../../../experimental/dorkbook/defaults.py#L96-L97)), not
  in the dialog's run-local submit path. Shodan filters are far shorter than 500
  in practice; the cap is a guard against accidental pastes. Strip surrounding
  whitespace before validating/submitting.

---

## Objections / spec discrepancies (raised per charter)

None block implementation, but RA/DA must know:

1. **Grey-out is not an existing per-protocol pattern** (spec Decision 3 wording).
   Verified: unchecking a *protocol* checkbox does **not** disable its Max cell
   today; `command=_refresh_protocol_estimate_lines` only updates estimate labels.
   The only disable path is **provider-level** (`_sync_shodan_options_state` →
   `sync_option_entries` → `_apply_state_recursive`). **Recommendation:** proceed
   — per-protocol grey-out is HI's explicit intent — but implement it as *new*
   wiring (a `trace` on each `protocol_<p>_var` that disables that row's Max+Query
   entries). Side effect: the existing Max cells will now also grey when their
   protocol is unchecked (a small, intended-looking change). Reuse the
   `state=disabled` mechanism, not a nonexistent per-protocol one.

2. **Layout restructure, not an addition.** Today SMB/FTP/HTTP render
   *horizontally* (checkbox+Max in row 0, cols 0–5). Option A is a *vertical*
   per-protocol grid. The Max entries move; `test_unified_scan_dialog_layout.py`
   geometry/alignment assertions will need updating (Card 2).

3. **Dialog cannot reuse the key read helper.** `read_shodan_api_key_from_config`
   and the gate are `dash`-scoped; the Start Scan dialog is not a dashboard but
   exposes `self.config_path`. **Recommendation:** for prefill/reconcile display,
   read via `SMBSeekConfig(self.config_path).get_shodan_api_key()`
   ([config.py:471](../../../shared/config.py#L471)) per AGENTS.md ("always access
   config through SMBSeekConfig"). The dialog still only *sets* `api_key_override`
   in the request; the `dash`-scoped gate (which has `dash`) consumes it downstream.

4. **No scan_manager-level override test exists** asserting the override→config
   mapping by name; coverage today is at the dialog/gate and downstream-budget
   layers. Card 1 adds that missing scan_manager coverage (tests-as-spec).

5. **Var naming:** spec suggested `shodan_smb_query_var`. Existing convention is
   `smb_max_results_var` / `provider_shodan_var`. **Recommendation:** name the new
   vars `smb_shodan_query_var` / `ftp_shodan_query_var` / `http_shodan_query_var`
   and `shodan_api_key_var` for consistency. Cosmetic; DA's call.

---

## Elevated-risk (credential surface)

The API key is a secrets surface (Development SOP Elevated-Risk list: secrets /
tokens). Card 4 isolates it in its own commit for RA scrutiny. Constraints:

- **No new secret-writing code.** Persistence stays in the existing audited gate
  (Q1). The dialog only reads for display and sets the run-scoped
  `api_key_override`.
- **Mask by default** (`show="•"`); reveal is a transient view toggle, not stored.
- **Never log the key**; never echo it into messageboxes.
- **No new `grab_set` / no new Toplevel.** Key row and query grid are inline in
  the existing dialog (which uses `transient` + `ensure_dialog_focus`, not
  `grab_set` — verified at [unified_scan_layout.py:99](../../../gui/components/unified_scan_layout.py#L99)).
  Keymaster opens via the existing modeless `show_keymaster_window`. So the
  `ensure_dialog_focus`-on-`grab_set` rule needs no new wiring; if DA introduces
  any new `grab_set` Toplevel, it must call `ensure_dialog_focus`.
- **Keep Shodan mocked in tests** — never hit the network (AGENTS.md hard no-no).

---

## Plan — task cards (≈ one commit each)

Build order: **1 → 2 → 3 → 4 → 5**. Card 3 depends on 1 (scan_options keys) and 2
(query vars/entries). Card 4 edits the builder Card 2 creates. One card, one
patch; do not start the next until the current is accepted.

### Card 1 — Backend: run-scoped per-protocol `base_query` overrides

**Files:** [scan_manager.py](../../../gui/utils/scan_manager.py) (workers at 352 /
1013 / 1167); new test file `gui/tests/test_scan_manager_shodan_query_override.py`.

**Do:** in each per-protocol worker, after the existing `max_results` block,
inject a `base_query` override when `scan_options` carries a non-blank query for
that protocol, using the verified nested-dict shapes (SMB under `shodan`, FTP
under `ftp.shodan`, HTTP under `http.shodan`, all `query_components.base_query`).
Mirror the `max_results` `setdefault(...)` idiom; inject only when provided.
Define the scan_options key names now (recommend `smb_shodan_query` /
`ftp_shodan_query` / `http_shodan_query`) — Cards 2–3 produce them.

**Acceptance:**
- Given scan_options with each protocol query, the worker's `config_overrides`
  contains the correct nested `base_query`; absent/blank → not injected.
- Existing `api_key_override` and `max_results`/credit-budget overrides unchanged.
- No downstream resolver change (readers already consume these paths).

**Tests touched:** new `test_scan_manager_shodan_query_override.py` asserting all
three mappings by name (Shodan mocked via the `sys.modules["shodan"]` stub
pattern from `shared/tests/test_protocol_shodan_query_budget.py`). Smoke the
existing [test_scan_manager_shodan_cap.py](../../../gui/tests/test_scan_manager_shodan_cap.py)
still passes.

### Card 2 — UI: Shodan per-protocol query grid + grey-out (layout + vars + static prefill)

**Files:** add `build_shodan_sub_panel` to
[scan_provider_options.py](../../../gui/components/scan_provider_options.py)
(mirroring `build_searxng_sub_panel`); have
[unified_scan_layout.py:359-406](../../../gui/components/unified_scan_layout.py#L359-L406)
delegate to it (keeps `unified_scan_layout.py` from growing, follows the
established extraction pattern). Declare new query vars in
[unified_scan_dialog.py](../../../gui/components/unified_scan_dialog.py) `__init__`
near the max-results vars (128-130).
[test_unified_scan_dialog_layout.py](../../../gui/tests/test_unified_scan_dialog_layout.py).

**Do:** restructure the Shodan panel into the Option A vertical grid — one row per
protocol: `checkbox | Max entry | Query entry`, aligned `Max | Shodan query`
columns; keep the **single** `Dorkbook…` button and the estimate row (relocated
below the grid; estimate logic unchanged). Prefill each Query from
`read_defaults(self.config_path)["shodan:<PROTO>"]` (static here; reconcile in
Card 3). Wire per-protocol grey-out: a `trace`/command on each
`protocol_<p>_var` sets that row's Max+Query entries `state=disabled` when
unchecked (greys, does not hide). Named theme styles only; `safe_messagebox`
only; no new `grab_set`.

**Acceptance:**
- Panel renders the vertical grid; Max and Query columns align across rows.
- Unchecking a protocol greys that row's Max+Query; re-checking re-enables.
- Single `Dorkbook…` button retained; estimate row + "How?" link still present
  and functional; max-results behavior unchanged.
- No scroll regression (layout test); file-length guard respected
  (`scan_provider_options.py` well under 1700).

**Tests touched:** update `test_unified_scan_dialog_layout.py` for the new
geometry/alignment and the per-protocol disabled-state; keep
`test_unified_scan_dialog_validation.py::test_inline_max_results_vars_flow_into_build_scan_request`
green.

### Card 3 — Query reconcile + validation + scan-request wiring

**Files:** extend [dorkbook_events.py](../../../gui/components/dorkbook_events.py)
with `refresh_shodan_queries(dialog, *, applied=None)` + `bind_shodan_queries`
(handling `shodan:SMB/FTP/HTTP` destinations); call them alongside the
self-hosted bind at
[unified_scan_dialog.py:141-144](../../../gui/components/unified_scan_dialog.py#L141-L144).
Add per-protocol query to `_build_scan_request`
([unified_scan_dialog.py:941-1060](../../../gui/components/unified_scan_dialog.py#L941-L1060))
and validation near the self-hosted rule (976-979).
[test_unified_scan_dialog_validation.py](../../../gui/tests/test_unified_scan_dialog_validation.py).

**Do:** mirror the self-hosted reconcile (Q2): per-protocol baseline
`_shodan_<proto>_default`; FocusIn/`<<DorkbookApplied>>` refresh reconciles so an
edit survives; a `shodan:<PROTO>` Apply replaces that row. In `_build_scan_request`,
for each *selected* protocol put its stripped query into the scan_options keys
Card 1 consumes; validate non-blank-when-selected and ≤500 chars (Q3).

**Acceptance:**
- Edited row survives a FocusIn/Dorkbook refresh; a Dorkbook Apply to that
  protocol replaces it and resets its baseline; other rows untouched.
- Blank query for a selected protocol → `ValueError` with a clear message; blank
  for an unchecked protocol → no error. Query >500 chars → rejected.
- Request carries `smb/ftp/http_shodan_query` for selected protocols only; they
  flow to the Card 1 overrides end-to-end.

**Tests touched:** add validation + reconcile + request-wiring tests to
`test_unified_scan_dialog_validation.py` (and/or a small
`test_unified_scan_dialog_shodan_query.py`); Shodan not invoked (dialog-level).

### Card 4 — Shared masked API-key row (Elevated-Risk; isolated commit)

**Files:** `build_shodan_sub_panel` in
[scan_provider_options.py](../../../gui/components/scan_provider_options.py) (add
the key row at panel top); `shodan_api_key_var` in
[unified_scan_dialog.py](../../../gui/components/unified_scan_dialog.py);
`api_key_override` wiring in `_build_scan_request`/`_start`; reconcile/FocusIn in
the Card 3 binding. New `gui/tests/test_unified_scan_dialog_shodan_key.py`;
reuse patterns from
[test_dashboard_api_key_gate.py](../../../gui/tests/test_dashboard_api_key_gate.py).

**Do:** add the shared row — masked `ttk.Entry(show="•")` + reveal toggle (flips
`show` to/from `""`) + `Keymaster…` button calling
`show_keymaster_window(self.dialog, settings_manager=self._settings_manager,
config_path=str(self.config_path))`. Prefill the field from
`SMBSeekConfig(self.config_path).get_shodan_api_key()` (objection 3); refresh on
FocusIn with reconcile semantics (keep an in-progress edit; snap to latest stored
only if untouched) so a key applied via the modeless Keymaster appears without
reopening. On launch, set `scan_options["api_key_override"]` from the field when
non-blank (Q1) — **no new persistence**; the existing gate handles save/prompt.

**Acceptance:**
- Field prefilled from stored key, masked by default; reveal toggles visibility
  (never persists the revealed state); key never logged.
- Keymaster button opens the modeless window; after Apply, a FocusIn refreshes
  the field without clobbering an in-progress edit.
- Inline value populates `api_key_override`; first-time entry persists via the
  gate; an edit when a key already exists is run-scoped and does **not** overwrite
  `shodan.api_key`.
- No new `grab_set`; Shodan mocked / avoided in tests.

**Tests touched:** new `test_unified_scan_dialog_shodan_key.py` (prefill, mask,
reveal, FocusIn-refresh-after-simulated-apply, override-set); confirm
[test_dashboard_api_key_gate.py](../../../gui/tests/test_dashboard_api_key_gate.py)
still passes (persist-on-first-entry / no-overwrite).

### Card 5 — Docs closeout + manual-QA checklist (RA-owned acceptance)

**Files:** [README.md](README.md), [SHODAN_SCAN_FIELDS.md](SHODAN_SCAN_FIELDS.md)
(status → implemented), this plan (mark cards done). Per charter, docs closeout
and the acceptance commit are **RA's**, not DA's.

**Do:** update the task index/status, record the HI manual-test steps (below), and
note any deltas between plan and landed code.

**Acceptance:** README points at the landed work; spec/plan statuses accurate;
disposition line ready for the `development → main` PR per AGENTS.md.

---

## Validation

Per-card (Shodan always mocked — never hit the network):

```bash
# Card 1
./venv/bin/python -m pytest gui/tests/test_scan_manager_shodan_query_override.py gui/tests/test_scan_manager_shodan_cap.py
# Card 2
./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_layout.py
# Card 3
./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_validation.py gui/tests/test_unified_scan_dialog.py
# Card 4
./venv/bin/python -m pytest gui/tests/test_unified_scan_dialog_shodan_key.py gui/tests/test_dashboard_api_key_gate.py
# Full suite before the acceptance commit
./venv/bin/python -m pytest
```

Guardrail suites that must stay green (touch GUI conventions): messagebox and
theme-style guardrails run as part of the full suite.

**Manual (HI), via the only entrypoint `./dirracuda`):** (API-key row is Design B
after Card 6 — a sentinel status field, no reveal; the stored key is never shown.)
1. Open **Start New Scan**; select Shodan. Confirm the **API key** row shows a
   status token (`<CONFIGURED>` if a key is stored, else `<not set>`) and a
   `Keymaster…` button — **no Show/reveal control** — plus the per-protocol
   `Max | Query` grid with saved dorks prefilled.
2. Uncheck HTTP → its Max+Query grey out (stay visible); re-check → re-enable.
3. Edit the SMB query, open Dorkbook, refresh/focus back → edit survives. Apply a
   Dorkbook SMB dork → the SMB row updates to the applied value.
4. **API key (Design B):** the field never shows your stored key. Click it → it
   clears to a masked (`*`) override box; type a one-off key and run a (mocked)
   scan → the run uses the typed key and `shodan.api_key` in config is unchanged.
   Leave the field as `<CONFIGURED>` (don't type) → the scan uses your saved
   key. With no key, add one via `Keymaster…`, focus back → the token flips
   `<not set>` → `<CONFIGURED>` without ever displaying the key.
5. Blank a selected protocol's query → Start Scan is rejected with a clear message.

---

## Risks

- **Credential surface (Elevated):** mitigated by reusing the audited gate,
  masking by default, no logging, isolating Card 4. RA ≠ DA session/vendor.
- **Layout regression:** vertical restructure moves the Max entries; covered by
  updated layout tests + manual step 1–2.
- **Reconcile drift:** reusing `reconcile_query` and the exact self-hosted bind
  pattern keeps Shodan and Self-hosted behavior identical; covered by Card 3 tests.
- **File-length:** all touched files stay well under 1700 after changes
  (`scan_manager` ≈1292, `scan_provider_options` ≈≤720, `unified_scan_dialog`
  ≈1191, `unified_scan_layout` ≈ flat, `dorkbook_events` ≈87).

## Rollback

Each card is a single commit; revert the card's commit to undo it. No schema or
data migration, so revert is clean. Cards 1–4 are additive to a run-scoped path;
reverting any one leaves the rest functional except its dependents (revert in
reverse order 4→1 if backing out the whole feature).

---

## Grounding (verification summary, 2026-10-06)

Three Explore passes over the running code confirmed the spec's references.
Exact matches: scan_manager workers at **352 / 1013 / 1167**, ctx mgr at
**interface.py:848**, dialog **1163** lines, config paths and `DORK_*` as cited.
Corrections folded into this plan: (a) per-protocol grey-out does **not** exist
today — it is provider-level only; (b) the Shodan panel is **horizontal** today —
Option A is a restructure; (c) the key gate also **persists** `shodan.api_key`
(not override-only) and is `dash`-scoped; (d) the 500-char cap is Dorkbook-only,
so the run-local cap is new; (e) no scan_manager override test exists today
(Card 1 adds it). The override ctx mgr takes **nested dicts**, not dotted keys;
the **`max_results`** block (not `api_key`) is the correct template for
per-protocol `base_query`.

---

## RA Closeout (2026-10-06)

All four cards implemented by DA sessions, each independently reviewed by RA
(diff read + RA re-run of the named tests, not DA's word), committed on
`development`. **Not pushed — push reserved to HI.**

| Card | Commit | What landed |
|---|---|---|
| 1 | `899ce7e` | scan_manager: run-scoped per-protocol `base_query` overrides (SMB/FTP/HTTP), mirroring the `max_results` block; new override test. |
| 2 | `166ed13` | Shodan panel restructured to the vertical `checkbox \| Max \| Query` grid + per-protocol grey-out; static query prefill; `DEFAULT_GEOMETRY` 720→800. |
| 3 | `461a256` | Query reconcile (mirror Self-hosted; `shodan:<PROTO>` destinations), validation (required-when-selected + ≤500), request wiring + `build_protocol_scan_options` propagation. |
| 4 | `cb7f0d1` | Shared masked API-key row (Elevated-Risk): `show="•"` + reveal toggle + Keymaster button; non-raising prefill; `api_key_override` run-scoped via the **unchanged** gate; FocusIn reconcile; `DEFAULT_GEOMETRY` 800→824. **(Reveal behavior superseded by Card 6.)** |
| fix | `b125386` | Test-harness follow-up to Card 3: the full-suite run caught `test_unified_scan_dialog_searxng_controls.py`'s own `_make_dialog` missing the query vars; set them non-blank (test-only). |
| 6 | `a81c89f` | **HI testing finding → redesign (Elevated-Risk).** Card 4's `Show` toggle revealed the stored key in plaintext without the Keymaster passphrase. HI chose **Design B**: the API-key field is a non-secret sentinel status token (`<CONFIGURED>`/`<not set>`), **no reveal control**; focusing it gives a masked one-off override box. The stored key is never read into the field (only a boolean presence check). Independent security re-check (separate session): **SHIP, bypass closed**. |

**Open questions — resolved and implemented as recommended:** Q1 run-scoped via
the existing gate (no new secret-writing code; first-time entry persists, later
inline edits never overwrite the saved key); Q2 Self-hosted reconcile mirrored
exactly; Q3 required-when-selected + a new 500-char run-local cap.

**Elevated-Risk sign-off:** Card 4 (credential surface) had an independent
security review in a separate session — verdict **SHIP**, no defects. Eight
properties PASS: masked-by-default; key never logged/argv/title/template/
settings; gate unchanged; non-raising prefill; correct run-scoped Q1 flow; no
new `grab_set`; correct provider-level grey-out; temp-config exposure is
pre-existing (owner-only `0600`, deleted in `finally`).

**Validation (RA, actual):** gui/tests → **2498 passed, 8 skipped, 3 failed**.
All 3 failures are **pre-existing and environmental**, confirmed by running them
at the pre-Shodan baseline `5fbf801` (they fail identically there): the Start
Scan geometry-restore test (`test_start_scan_remembers_dragged_position_on_reopen`
— WM title-bar Y offset) and two `test_analyst_*` tests (window-height assertion
+ analyst service logic). The Shodan work touches **no** analyst/webui/shared
files, so the broader full-suite failures (missing `pydantic`/`openai`; webui
sandbox) are out of scope. Guardrails (messagebox, theme-style) green.

**Security finding during HI testing (resolved):** Card 4's `Show`/reveal toggle
let the stored key be viewed in plaintext without the Keymaster passphrase.
Card 6 (`a81c89f`) removes the reveal entirely and redesigns the field so the
stored key is never displayed (Design B, chosen by HI); an independent security
re-check confirmed the bypass is closed. The earlier "re-mask on provider-off"
polish is now moot — there is no reveal control at all.

**Ready for HI testing** via `./dirracuda` using the Manual steps in §Validation
above. HI still owns the push of all `development` commits.
