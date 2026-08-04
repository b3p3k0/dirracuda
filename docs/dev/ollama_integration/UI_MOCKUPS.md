# Ollama Integration — UI Mockups (Draft)

Date: 2026-08-04
Status: Layout draft. Behavior contracts are frozen in [`CONTRACT.md`](CONTRACT.md)
§14; where a mockup below conflicts with the contract, the contract wins. Two
behaviors changed after these sketches were first drawn — see the C0A notes inline.

Working name: **Analyst** (the "digital intern").

## Integration Architecture

Follows the Sherlock dual-surface precedent exactly:

1. **Standalone** — an Accessories tab and window that points at any directory of
   already-extracted files. This is the "analyze what we already pulled" path and
   the primary surface.
2. **Post-extract hook** — after a bulk extract completes, an optional "Run
   Analyst on these files" step, reading straight from the quarantine path the
   extract just wrote (tmpfs or disk). Mirrors Sherlock's post-probe hook.
3. **Server List action** (later card) — "Analyze Extracted" on a host row, for
   hosts that have a prior extract on disk.

Both entry paths converge on the same runner and the same per-host report. One
code path, two doors.

## Design Goals (from HI)

- Minimum operator input.
- Predictable, standardized result format.
- Reads from tmpfs quarantine OR an existing directory.
- Exports reports and selected findings to a configurable target directory.

---

## Screen 1 — Accessories: Analyst tab

The tab is the low-input launcher. Two source modes, one output field, Start.

```
+-- ⚗ Accessories ------------------------------------------------[_][x]--+
| [SearXNG] [Reddit] [Web UI] [Dorkbook] [Keymaster] [Sherlock] [Analyst] |
|                                                                          |
|  Analyst — offline document review                                       |
|  Reads extracted documents, finds PII / financial / contact data,        |
|  and writes a standardized per-host report. Uses loopback Ollama only.   |
|                                                                          |
|  Source                                                                  |
|   ( ) Latest extract (tmpfs quarantine)     [ 313 files ready ]          |
|   (o) A directory of extracted files                                     |
|       [ /home/kevin/Documents/Extracted/174.54.32.233     ] [Browse...]  |
|       Detected: 258 documents across 1 host  ·  55 unsupported           |
|                                                                          |
|  Output                                                                  |
|   Reports + findings exports ->                                          |
|       [ /home/kevin/Documents/Extracted           ] [Browse...]          |
|                                                                          |
|  Model:  gpt-oss:20b            [ Change... ]   ● Ollama reachable        |
|                                                                          |
|                                        [ Open Reports ]   [ ▶ Analyze ]  |
+--------------------------------------------------------------------------+
```

Notes:
- Source radio auto-selects "Latest extract" only when a quarantine run exists
  this session; otherwise it is disabled with a greyed hint and directory mode is
  default.
- The "Detected: N documents / M unsupported" line updates on directory pick — a
  fast pre-scan (extension + magic sniff, no parsing) so the operator sees scope
  before committing. This is the honesty-up-front principle from the coverage
  contract, surfaced at input time.
- Model line shows a live reachability dot. Red + disabled Analyze button when
  Ollama is unreachable, with the reason on hover.
- Everything except Source and Output has a sane default. Two clicks to run.

---

## Screen 2 — Advanced (Change... / gear), collapsed by default

Only opened by operators who want to tune. Nothing here is required.

```
+-- Analyst — Advanced --------------------------------------[_][x]--+
|                                                                    |
|  Model                                                             |
|   Primary model    [ gpt-oss:20b            v ]  (installed)       |
|   Thinking         [ ] enable (slower; off for batch runs)         |
|                                                                    |
|  Scope                                                             |
|   File types   [x] RTF  [x] PDF  [x] Word  [x] Excel  [x] Text     |
|   Max file size (MB)        [ 100  ]                               |
|   Per-file parse timeout (s)[ 30   ]                               |
|                                                                    |
|  Depth                                                             |
|   (o) Fast   — detectors on all files, model on flagged files only |
|   ( ) Deep   — model reads every supported file (much slower)      |
|                                                                    |
|  Output                                                            |
|   Report format   [x] HTML   [x] JSONL   [x] CSV (findings table)  |
|                                                                    |
|                                          [ Cancel ]   [ Save ]     |
+-------------------------------------------------------------------+
```

> **C0A note (changed):** original-document copying is OUT of the V1 MVP
> (materially expands the security surface). The old "Copy documents…" checkboxes
> are removed. U4 exports selected **report rows/findings** to CSV/JSONL, not file
> copies. The ClamAV clean/infected/unknown copy contract is deferred with that
> feature.

Notes:
- "Fast vs Deep" is the two-phase decision (RESEARCH_NOTES design consequence 7)
  exposed as a single, plain-language choice. Fast is the default and the one the
  scale numbers argue for.
- Model dropdown is populated from `/api/tags` at open — only installed,
  non-cloud models appear. No free-text model entry (guards R1/R14).

---

## Screen 3 — Progress (Running Tasks monitor)

Reuses the existing scan/probe/extract monitor dialog and Running Tasks registry.
Hiding it does not stop the job (existing contract).

```
+-- Analyst: 174.54.32.233 ----------------------------------[_][x]--+
|                                                                    |
|  Phase 1 complete — 241/258 detector-scanned; 17 terminal failures |
|  Phase 2 model review [#####################---------] 130/183 71% |
|                                                                    |
|  Inventory   258 / 258 files                                       |
|  Detectors   241 / 258 found    ·  1,204 identifier hits           |
|  Model       130 / 183 selected ·  est. 6 min remaining            |
|                                                                    |
|  Coverage so far                                                   |
|   terminal 205  ·  pending 53  ·  no text layer 9  ·  parse failed 3|
|                                                                    |
|  Recent                                                            |
|   ✓ tax_return_2019.pdf        HIGH  SSN, bank acct                |
|   ✓ resume_j_smith.docx        MED   contact, employer            |
|   ⚠ scan0042.pdf               no text layer                       |
|   ✓ menu.rtf                   —     no findings                   |
|                                                                    |
|                                   [ Hide ]   [ Cancel Analysis ]   |
+-------------------------------------------------------------------+
```

Notes:
- Two progress lines because there are two phases with different speeds. The
  live coverage counters are the "we are actually going through everything"
  guarantee made visible while it runs.
- Cancel checkpoints and stops; completed files are not re-done on resume (R9).

---

## Screen 4 — The report (HTML, opens in system browser or file viewer)

Standardized top-to-bottom. Same shape for a 47,000-doc host and a 50-doc host.

```
================================================================
  ANALYST REPORT — 174.54.32.233
  Generated 2026-08-04 14:22 · model gpt-oss:20b · Fast mode
================================================================

COVERAGE
  258 documents found · 258 terminal outcomes
  241 detector-scanned (93%) · 183 model-reviewed (71%; 100% selected)
  58 detector-only · 9 no text layer · 3 parse failed · 5 unsupported
  9 scanned PDFs need OCR (not available).

EXPOSURE SUMMARY
  ┌────────────────────┬───────┬───────────────────────────────┐
  │ Category           │ Files │ Highlights                    │
  ├────────────────────┼───────┼───────────────────────────────┤
  │ Government ID (SSN)│    14 │ 22 SSNs across 14 files       │
  │ Financial / bank   │     9 │ 6 bank accts, 3 card numbers  │
  │ Tax                │     7 │ W-2, 1040 forms 2017-2020     │
  │ Contact / PII      │    88 │ names, emails, phones, addrs  │
  │ Medical            │     4 │ referenced in 4 documents     │
  └────────────────────┴───────┴───────────────────────────────┘

TOP FINDINGS
  1. HIGH  employees_2019.xlsx  · sheet 2 · 41 rows
     22 SSNs, 14 full names, 14 home addresses.
     Evidence: "SSN" column header, rows 2-42.
  2. HIGH  tax_return_2019.pdf  · p.1
     SSN 123-45-6789, bank routing + account.
     Evidence: "Your social security number ... 123-45-6789"
  ...

DOCUMENT INVENTORY (sortable table: file · type · category · risk · state)
  ...

  Report written to:
    /home/kevin/Documents/Extracted/_analyst/174.54.32.233/
================================================================
```

Notes:
- Coverage is the first section, always. A report that leads with findings and
  hides what it skipped is the exact failure mode HI called out.
- Raw values shown (locked decision 1). The static HTML uses context-appropriate
  escaping, no JS, no remote assets, and a strict CSP (R5); canonical unmodified
  evidence lives only in the 0600 JSONL.
- The JSONL alongside the HTML feeds a future `dirracuda.db` import without
  re-running anything (D5 forward-compat).

---

## Output Folder Layout

```
<output_dir>/_analyst/<host>/       (0700; files 0600)
  report.html                       (derived, sanitized, CSP-locked)
  findings.jsonl                    (canonical unmodified evidence)
  findings.csv                      (derived, spreadsheet-safe)
  run.json                          (versions, model digest, coverage totals)
```

> **C0A note (changed):** no `documents/` folder in the MVP — original-document
> copying is deferred. `_analyst/` is excluded from future analysis runs so reruns
> never ingest their own output. The prefix keeps generated output from colliding
> with the source tree and makes it one thing to move or delete.

## UI Decisions

| ID | Question | Lean |
|----|----------|------|
| U1 | New Accessories tab, or fold into an existing one | **Resolved:** new tab (matches Sherlock) |
| U2 | Post-extract hook auto-prompt, or opt-in toggle in extract dialog | **Resolved:** opt-in toggle, off by default (Sherlock C5.1 precedent) |
| U3 | Show live findings in the monitor, or only in the final report | **Resolved:** show recent findings (builds trust it is working) |
| U4 | Copy flagged docs, or export findings | **Resolved: export findings** rows to CSV/JSONL via a per-row checkbox picker (select all/none). No original-document copying in the MVP. |
| U5 | One report per host always, or a combined index across hosts | **Resolved for V1:** per-host reports; combined index deferred |

## Reduced-isolation note (C0A)

If bubblewrap is ever unavailable, preflight fails by default. The optional
reduced-isolation mode is a per-run, non-persistent acknowledgement, is
**unavailable to the automatic post-extract hook**, is recorded in run/report
metadata, and is barred from the benchmark and acceptance tests. It runs the
parser under the user's normal OS account on hostile input — surfaced with an
explicit warning, never a quiet default.
