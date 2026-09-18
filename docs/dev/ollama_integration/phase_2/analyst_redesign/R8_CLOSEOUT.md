# R8 — Read-First Redesign Closeout

- Date: 2026-09-18
- Branch: `feature/ollama-analyst` (NOT pushed). Awaiting merge review.
- Status: read-first redesign implemented across R0–R4d and validated per card.

## What shipped

Analyst is now read-first. A run reads a host's extracted files and writes `report.json`: a
senior-tech read (what the host is, likely owner, contacts, HIGH/MED/LOW risk, top exposures)
over a grounded, ranked facts layer. Reports render to Markdown/text/HTML/CSV, export anywhere,
copy to clipboard, and batch-export. A moved report opens with a "changed since saved" badge.
The model is user-selectable from a server-reported dropdown; the exact-pin relaxed to a per-run
recorded selection with a fail-closed digest-integrity check; loopback-only and cloud-tag
rejection stay.

## Card ledger (each codex-implemented, Claude-validated, committed)

| Card | What | Commit theme |
|---|---|---|
| R0 | contract freeze (docs) | read-first contract, report.json schema, UI contract |
| R1 | pure read + facts models, prompts (pinned SHAs), report.json builder | pure layer |
| R2 | sidecar schema v4 read tables | analyst_read/exposures + fact_rank |
| R3a | schema v5 host-read contact ledger | analyst_read_contact |
| R3b | host READ reduce engine (best-effort, charged, fenced) | read_reduce.py |
| R3c | finalize writes report.json + fallback + ranking | source of truth |
| R5 | render layer (md/txt/html/csv) + warn-not-block seal | report_render.py |
| R6a | simplified launcher + Advanced dialog + Result risk column | tab reshape |
| R6b | batch export of selected runs | report_export.py |
| R7 | two-layer report view + export + copy | report window |
| R4a | schema v6 discovery ledger + discovered-model list | analyst_discovery_contact |
| R4b | model discovery backend (charged /api/tags list + persist) | discover_models |
| R4c | per-run model selection (relax exact pin) [HI-reviewed] | model identity |
| R4d | Advanced model dropdown wiring | connect/select/use |

Contract amendments: A1 (reuse worksheet-v2 as the facts map), A2 (pre-run discovery ledger).

## Validation evidence (real venv; areas run separately per the known cross-suite flake)

- Shared+experimental (no Tk): 3277 passed (1 known pre-existing daemon-import ordering flake in a
  single mixed process only; see reference note).
- experimental alone: 673 passed, 0 failed.
- gui alone (xvfb): 2042 passed, 0 failed.
- Analyst shared subset: 1531 passed, 0 failed. Analyst GUI subset: 35 passed, 0 failed.
- Every code card was validated in the real venv with the diff read by Claude; the R4c pin
  relaxation was diff-reviewed and approved by the operator before commit.

## Screenshots (xvfb)
- Launcher: `img/analyst_launcher.png`
- Advanced (model dropdown): `img/analyst_advanced.png`
- Report view (two layers): `img/analyst_report.png`

## File-size audit (limit 1700)
Largest touched files: checkpoint.py 1426, ollama_state.py 1288, db_schema.py 1272,
analyst_tab.py 1248, extract.py 1149. All within limit.

## Known items / follow-ups
- Two pre-existing cross-suite flakes surface only when the whole repo runs in one pytest
  process (a Tcl_AsyncDelete teardown abort and a tkinter-in-sys.modules ordering failure of
  `test_daemon_modules_import_without_tkinter`). Run areas separately for clean summaries.
- Legacy pre-redesign runs have no report.json and show "legacy - re-run to view" (re-run
  everything, by decision).
- Remote model server stays a separate, later, reviewed card (loopback-only for now).
- Main project docs (README/AGENTS/TECHNICAL_REFERENCE) should be synced to read-first at merge
  time; this closeout keeps the redesign docs authoritative on the branch.
