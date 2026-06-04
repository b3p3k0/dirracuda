# Integrate Experimental Features Into Main - ROADMAP

Status: Active
Mode: PA/RA supervised, DA implemented, one card at a time
Last updated: 2026-06-04

## Card Status

| Card | Title | Status |
| --- | --- | --- |
| C0 | Baseline Contracts Freeze | COMPLETE |
| C1 | Accessories Shell Cutover | COMPLETE |
| C2 | Core Provider Registry For Start Scan | COMPLETE |
| C3 | SearXNG Core Promotion Path | COMPLETE |
| C4 | Reddit Core Promotion Path | COMPLETE |
| C5 | Database Surface Consolidation | COMPLETE |
| C6 | Provider-Scoped Config Localization | COMPLETE |
| C7 | Runtime Hardening + Regression | COMPLETE |
| C8 | Docs/Reference Closeout | COMPLETE |
| C9 | SearXNG Hard Cutover To Primary DB | COMPLETE |
| C10 | Reddit Hard Cutover To Primary DB | COMPLETE |
| C10.1 | Reddit Anonymous RSS Cutover | COMPLETE |

## Phase A - Contract And Baseline

### C0 - Baseline Contracts Freeze (COMPLETE)

- Freeze current entrypoints, file-size risk zones, and test conventions.
- Record Censys suspension as explicit out-of-scope.

## Phase B - UX Promotion Scaffolding

### C1 - Accessories Shell Cutover

- Rename dashboard `Experimental` semantics to `Accessories`.
- Keep accessory modules (`Web UI`, `Dorkbook`, `Keymaster`) accessible.
- Do not yet remove legacy SearXNG/Reddit paths.

### C2 - Core Provider Registry For Start Scan

- Introduce provider-selection scaffolding for `Shodan`, `SearXNG`, `Reddit`.
- Keep existing SMB/FTP/HTTP scan path stable.

## Phase C - Provider Promotion

### C3 - SearXNG Core Promotion Path

- Add start-scan launch path for SearXNG with provider-owned validation and status.
- Wire probe/promote actions through existing safe paths.
- Update WebUI SearXNG surface to align with promoted main-flow contracts.

### C4 - Reddit Core Promotion Path

- Add start-scan launch path for Reddit with mode-aware validation.
- Wire probe/promote actions through existing safe paths.
- Update WebUI Reddit surface to align with promoted main-flow contracts.

## Phase D - Data + Config Consolidation

### C5 - Database Surface Consolidation

- Replace split DB controls with one DB entrypoint.
- Surface sidecar review/import under explicit legacy section.
- Add one-time desktop startup migration prompt for existing sidecar data.

### C6 - Provider-Scoped Config Localization

- Reduce global Shodan/provider clutter in app config.
- Move provider-specific controls to provider-owned flow where safe.
- Remove legacy sidecar DB exposure from WebUI operator pages; keep migration-status notice only.

## Phase E - Closeout

### C7 - Runtime Hardening + Regression

- Regression on touched scan/provider/database paths.
- File-size and modularization checks.

### C8 - Docs/Reference Closeout

- Update `README.md` and `docs/TECHNICAL_REFERENCE.md` to exact runtime truth.
- Update lessons learned and risk register outcomes.

## Phase F - Primary DB Cutovers

### C9 - SearXNG Hard Cutover To Primary DB (COMPLETE)

- New SearXNG runs write `dork_runs` / `dork_results` into the active primary DB context.
- Retained HTTP/HTTPS rows auto-sync into main HTTP server surfaces during run completion.
- Standard SearXNG browser mode hides manual promotion; legacy sidecar browsing remains for historical data.

### C10 - Reddit Hard Cutover To Primary DB (COMPLETE)

- New Reddit runs write `reddit_posts`, `reddit_targets`, and `reddit_ingest_state` into the active primary DB context.
- Parsed SMB/FTP/HTTP targets sync into primary protocol tables during run completion.
- Primary-backed Reddit browser mode hides manual promotion and clear actions.
- Legacy Reddit sidecar browsing remains available for historical data.

### C10.1 - Reddit Anonymous RSS Cutover (COMPLETE)

- Replace discontinued unauthenticated Reddit `.json` listing/search endpoints with public Atom/RSS feeds.
- Keep anonymous feed/search modes only; user/author mode is unsupported for new runs.
- Preserve C10 primary-DB write and sync flow; no sidecar write path restored.

## Execution Rules (All Cards)

1. Reproduce/confirm issue first.
2. State root cause explicitly.
3. Apply smallest safe fix.
4. Run targeted validation first; broaden by risk.
5. Report exact commands + PASS/FAIL.
6. No commit unless HI explicitly says `commit`.
7. If blocked, provide exact unblock commands and expected output.
8. If touched production code file >1700 lines, stop and propose modularization (tests/docs excluded from hard stop).
