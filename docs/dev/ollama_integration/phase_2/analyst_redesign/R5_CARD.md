# R5 — Report render layer + warn-not-block seal

- Date: 2026-09-18
- Type: code card. codex implements, Claude validates.
- Depends on: R3c (report.json exists). Enables R7 (report view) + export/copy.

## Goal

Render readable formats from report.json on demand (Markdown primary, plain text, HTML), and
relax the read-time integrity seal from fail-closed to warn-not-block for a CONTENT-hash change
only. Security tamper checks (symlink, ownership, mode) stay fail-closed.

## Deliverables

### 1. Pure renderers — `experimental/analyst/report_render.py` (new, pure stdlib)

Input is a validated report.json dict (call `report_json.validate_report_json` first). Output is
a string. No I/O.

- `render_markdown(report: dict) -> str` (PRIMARY): a two-layer document — the read block first
  (WHAT THIS IS, Risk, Likely owner, Contacts, Files read/Flagged, TOP EXPOSURES), the exact
  `unverified_notice` line, then a FACTS table (Kind | Value | File | Rank). Mirror UI_CONTRACT.md
  section 3 wording. Quotes rendered verbatim inside a code span so markdown cannot execute them.
- `render_text(report: dict) -> str`: the same content as plain text.
- `render_html(report: dict) -> str`: self-contained, the frozen `HTML_CSP`, inline `<style>`,
  no JS, no remote assets. Every value goes through HTML escaping (text nodes only) — the
  Sherlock rule. Quotes/owner/summary are attacker-influenced; escape all of them.
- `render_facts_csv(report: dict) -> str`: the facts table as spreadsheet-safe CSV, reusing the
  existing `report_contract.csv_safe` guard.
- A `render(report, fmt)` dispatcher over `{"md","txt","html","csv"}`.

### 2. Warn-not-block seal — narrow relaxation

Today `report.verify_completed_report` raises on a manifest-hash mismatch, and
`service.completed_report_html` raises before returning the path. Change ONLY the content-hash
mismatch into a warning:

- Add `report.open_completed_report_relaxed(run_id, *, path=None) -> CompletedReadResult` (or
  extend the existing verify to return a result) that: runs the existing state check and
  `inspect_report_manifest` (which STILL raises on non-owner mode, 0700/0600 violation, or a
  symlink — keep those fail-closed), then compares the recomputed manifest SHA to the stored one.
  On mismatch it returns `changed=True` (does NOT raise); on match `changed=False`. It returns
  enough to locate report.json on disk.
- Add `service.read_report_json(run_id, *, path=None) -> tuple[dict, bool]` returning the parsed,
  validated report.json and the `changed` flag. It uses the relaxed opener; it still fails closed
  on symlink/ownership/mode tamper and on an unparseable/invalid report.json.
- Do NOT change the WRITE path (finalization) or `verify_completed_report`'s existing strict
  callers that must stay strict — instead ADD the relaxed accessor. If you make
  `completed_report_html` relaxed, it must still return the "changed" flag so R7 can badge it.

### 3. Tests — `shared/tests/test_analyst_r5.py`

- Renderers: markdown has the read block + exact unverified_notice + facts table; a `low` fact
  never appears in top exposures; HTML escapes an injected `<script>`/quote and carries the CSP;
  CSV cells with `=`/`+`/`@` are prefix-guarded; text/markdown/html/csv all render a valid report.
- Seal: a byte-changed artifact opens with `changed=True` (not raised); a symlinked or
  non-0600 artifact STILL fails closed; an invalid/foreign report.json fails closed.

## Constraints

- `report_render.py` is pure (no DB/IO/network). Escaping is mandatory on every rendered value.
- The seal relaxation is CONTENT-hash only. Symlink/ownership/mode tamper and invalid report.json
  stay fail-closed. Do NOT weaken the C12 symlink/tamper tests.
- No schema change. Files < 1700 lines. No raw doc content beyond report.json's bounded quotes.
- Allowed files: new `report_render.py`; additive accessors in `report.py` and `service.py`;
  tests `shared/tests/test_analyst_r5.py`. Do not modify the write/finalization path.

## Acceptance (Claude validates, real venv)

1. `./venv/bin/python -m pytest shared/tests/test_analyst_r5.py -q` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed (C12 tamper tests green).
3. HTML output contains the frozen CSP and escapes injected markup; markdown leads with the read
   and carries the exact unverified_notice.
