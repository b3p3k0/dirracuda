# Analyst Redesign — PA/RA Handoff Prompt

Copy the block below into the next planning session.

```text
You are a planning agent (PA) continuing Dirracuda Analyst work on branch
`feature/ollama-analyst`. This is a PLANNING session. Do not write product code.

The operator has frozen one objective: re-orient Analyst from a per-excerpt PII
classifier into a read-first tool that produces a senior-tech "read" of a host's
extracted files, plus a grounded facts layer, with exportable reports.

Read completely before acting:
- docs/dev/ollama_integration/phase_2/analyst_redesign/DESIGN_BRIEF.md   (start here)
- AGENTS.md
- docs/dev/ollama_integration/README.md
- docs/dev/ollama_integration/CONTRACT.md            (the frozen Phase 1 core you revise)
- docs/dev/ollama_integration/CONTRACT_ERRATA.md
- docs/dev/ollama_integration/RISK_REGISTER.md
- docs/dev/ollama_integration/LESSONS_LEARNED.md
- docs/dev/ollama_integration/phase_2/README.md
- Current code: experimental/analyst/worksheet.py, detectors.py, report_writer.py,
  report_browser.py, report.py; gui/components/experimental_features/analyst_tab.py;
  gui/components/analyst_report_window.py.

SOP (senior-review gate). This is a non-trivial feature.
- Plan first. Freeze a revised contract before any code.
- End every planning iteration with ExitPlanMode, including after each revision and after
  a rejection. Do not summarize a plan in prose instead.
- A review PASS authorizes only the one named card. Everything downstream stays HELD.
- The first card should be a docs/contract-freeze card (call it R0), not code.
- Verify any codebase claim against the shipped code before you encode it.

Decisions already frozen by the operator (do not relitigate):
1. Two-layer report: the read (model judgment, labeled unverified) over grounded facts.
2. Pipeline flips to read-first. Regex detectors stay, but only in the facts layer; they
   no longer gate the model.
3. report.json is the source of truth. Render Markdown (primary), plain text, HTML on
   demand. In-app display, Export, Copy, and batch export are required.
4. Integrity relaxes to warn-not-block. A moved report still opens with a "changed" badge.
5. Model pin relaxes. The user picks a model from a dropdown (server-reported). Record the
   model and digest per run.
6. Remote model server is a SEPARATE later card. Local ships first.
7. UI: simplified launch screen (one folder input), a separate Advanced dialog, and the
   report/export layouts in the brief. Use ASCII mockups in plan iterations; the operator
   has dyslexia and reads them better than prose.

Open questions to resolve in the plan (see brief section 13):
- Define Quick look vs Full read.
- How the model forms a host read without blowing context on a large host.
- The risk-rating rubric.
- How much of the current worksheet survives as the facts extractor.
- Owner-attribution wording that stays clearly unverified.
- report.json versioning and migration.

Standing engineering constraints (unchanged):
- Runtime entrypoint is only ./dirracuda. gui/main.py is a legacy import shim.
- Keep the GUI to CLI subprocess boundary and the reviewed detached Analyst worker.
- Use shared/path_service.py::get_paths() for all user-data paths.
- Keep the bubblewrap parser sandbox, worker lease/fence, two-attempt semantics, and
  fail-closed behavior everywhere except the three approved relaxations in brief section 10.
- Never expose private content in logs, reprs, exceptions, tests, prompts, or docs.
- Grounding uses substring containment. Do not trust model character offsets (C0B-1).
- Do not change dependencies, primary DB migrations, auth, CI, push, or branch state
  without the authorization AGENTS.md requires.
- Do not git push or commit unless the operator explicitly asks.

Communication:
- Write in Simplified Technical English. Short sentences, one idea each, active voice,
  lists over prose, whitespace. The operator has dyslexia.
- Use the completion format when reporting work: Issue / Root cause / Fix / Files changed /
  Validation run / Result / HI test needed.

First actions:
1. git status --short and confirm the worktree is clean.
2. Read the brief and the frozen CONTRACT.md. Note exactly which contract sections this
   redesign supersedes and which it keeps.
3. Draft the R0 contract-freeze plan: the revised analytical contract, the report.json
   schema (versioned), and the UI contract for the new dialog and report view.
4. Present R0 via ExitPlanMode. Do not start code. Wait for the operator's review.
```
