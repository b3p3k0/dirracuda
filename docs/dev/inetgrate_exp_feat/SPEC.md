# Integrate Experimental Features Into Main - SPEC

Status: Draft canonical contract (PA/RA maintained)
Last updated: 2026-05-25

## Objective

Promote the active experimental discovery surfaces (SearXNG and Reddit) into first-class desktop and WebUI scan workflows while preserving current SMB/FTP/HTTP behavior and keeping accessory tooling clearly separated.

## Locked Decisions

1. Censys promotion is **suspended** for this wave and excluded from implementation scope.
2. Canonical desktop entrypoint remains `./dirracuda`; `gui/main.py` remains shim-only.
3. Canonical WebUI entrypoint remains `./venv/bin/python -m experimental.webui.server`.
4. Delivery is one task card at a time: confirm -> fix surgically -> validate -> report -> wait.
5. No commits unless HI explicitly says `commit`.
6. We optimize for smallest safe deltas and runtime compatibility over broad refactors.
7. Legacy sidecar data migration is desktop-owned; WebUI surfaces are notification/status-only for migration state.

## In Scope

1. Promote SearXNG and Reddit from `Experimental` dialog-only workflows into core scan launch surface(s).
2. Replace dashboard `Experimental` button semantics with `Accessories` for non-core modules (`Web UI`, `Dorkbook`, `Keymaster`).
3. Consolidate database operator entrypoints behind one dashboard surface.
4. Standardize provider integration contracts for Shodan/SearXNG/Reddit.
5. Preserve and harden sidecar-to-main DB promotion/import flows.
6. Localize provider-specific settings to provider-owned UI/config surfaces.
7. Keep docs (`README.md`, `docs/TECHNICAL_REFERENCE.md`) synchronized with shipped behavior at each completed task.
8. Clean up WebUI provider surfaces so legacy sidecar DBs are no longer operator-facing there.
9. Add one-time sidecar migration notice/decision flow on desktop startup for existing sidecar data.

## Out Of Scope

1. Censys implementation, migration, or UI exposure.
2. Replacing desktop runtime as canonical.
3. Full package relocation of `experimental/se_dork` and `experimental/redseek` in this wave.
4. Broad DB schema rewrites that require risky one-shot migration.
5. Any unrequested redesign unrelated to feature promotion.

## Functional Requirements

### FR-1: Core Provider Exposure

- Start-scan experience must expose `Shodan`, `SearXNG`, and `Reddit` as launchable discovery providers.
- Censys must not appear in core provider selectors for this wave.

### FR-2: Provider-Scoped Options

- Each provider must expose its own required/optional settings with explicit validation.
- Missing required settings must block launch with actionable inline errors.

### FR-3: Deterministic Launch And Status

- Multi-provider runs must execute via existing safe orchestration boundaries (no shell command construction from user text).
- Runtime status and completion/errors must be surfaced per provider.

### FR-4: Database UX Consolidation

- Dashboard must expose a single DB entrypoint for:
  - main DB view path
  - DB tools path
  - legacy sidecar review/import path
- WebUI must not expose raw sidecar DB browsing as a normal operator surface after promotion.

### FR-5: Accessories Separation

- Dashboard accessory surface must hold non-core utilities only (`Web UI`, `Dorkbook`, `Keymaster`).
- SearXNG and Reddit must no longer be presented as accessory-only capabilities.

### FR-6: Legacy Compatibility

- Existing sidecar records and sidecar browsers remain available during transition.
- Sidecar import/promotion must be runtime-validated and reversible.
- Desktop runtime must provide a first-run migration notice when unmigrated sidecar data is detected.
- Migration notice requires explicit operator choice (yes/no now); no silent destructive behavior.

### FR-7: Config Locality

- Provider-specific keys/defaults move toward provider-owned UI/config sections.
- Global settings should not continue to accumulate provider-only controls.

### FR-8: WebUI Promotion Parity

- Promoted provider workflows in WebUI should operate against main DB-backed contracts.
- WebUI pages should stop presenting legacy sidecar DB browsing/import controls as primary operator actions.
- If migration is pending, WebUI should display a clear status notice directing operators to desktop migration flow.

### FR-9: One-Time Sidecar Migration Contract

- On desktop launch, if sidecar migration state is `not_started` and migratable rows exist, show a one-time migration notice with explicit options.
- `Yes` executes migration and records result summary/status.
- `No` defers migration and records explicit deferral status with permanent auto-prompt suppression for that install/session history.
- After `No`, migration can be run only via explicit manual operator action (no scheduled or repeated startup reminders).
- Migration status must be queryable by WebUI for notice rendering.

## Non-Functional Requirements

1. Security: keep subprocess calls argument-list based with `shell=False`; never execute untrusted shell strings.
2. Data safety: never assume schema shape; guard writes/reads by runtime table/column checks.
3. Compatibility: preserve behavior outside requested card scope.
4. Performance: avoid UI-thread blocking during provider runs/import operations.
5. Maintainability: if touched production code file exceeds 1700 lines, stop and propose modularization (tests/docs excluded from hard stop).

## Acceptance Criteria (Wave)

1. Operator can launch SearXNG and Reddit from core scan workflow without using Experimental dialog.
2. Accessories surface is renamed/re-scoped and no longer implies core provider dependency.
3. DB entrypoints are consolidated and include explicit legacy sidecar handling.
4. Censys remains suspended and absent from promoted workflows.
5. `README.md` and `docs/TECHNICAL_REFERENCE.md` match shipped behavior at wave completion.
6. WebUI no longer shows legacy sidecar DB surfaces as normal operator UI.
7. One-time desktop migration notice and migration-state notification path are implemented and documented.

## External Reality Checks (Planning Inputs)

- Shodan query-credit behavior and count/search semantics: `https://developer.shodan.io/api`
- SearXNG API endpoint/format/pagination constraints: `https://docs.searxng.org/dev/search_api.html`
- Reddit Data API use restrictions and rate-limit enforcement clauses: `https://redditinc.com/policies/data-api-terms`
- Python subprocess security guidance (`shell=True` caution): `https://docs.python.org/3/library/subprocess.html`
- SQLite URI mode behavior (`mode=rw`, no implicit create): `https://docs.python.org/3/library/sqlite3.html`
- Censys platform transition context (for suspension context only): `https://docs.censys.com/docs/platform-api-transition-guide`
