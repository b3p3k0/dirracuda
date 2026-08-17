# Analyst Phase 2 Agent Handoff Prompt

Copy the prompt below into the next agent session.

```text
You are continuing Dirracuda Analyst work on branch `feature/ollama-analyst`.

Before acting, read completely:
- AGENTS.md
- docs/dev/ollama_integration/README.md
- docs/dev/ollama_integration/CONTRACT.md
- docs/dev/ollama_integration/CONTRACT_ERRATA.md
- docs/dev/ollama_integration/RISK_REGISTER.md
- docs/dev/ollama_integration/LESSONS_LEARNED.md
- docs/dev/ollama_integration/phase_1/README.md
- docs/dev/ollama_integration/phase_2/README.md

Current boundary:
- Phase 1/C1–C15 is complete and archived under `phase_1/`.
- Public V1 release closure is commit `e5d40e2`.
- Mergerfs unsigned device/inode support and sidecar schema v3 are commit `7ee6f9e`.
- Live Analyst run-status fixes are commits `e41dc28` and `03ff840`.
- Ollama remains literal-loopback-only, cloud-disabled, and image-digest pinned.
- Raw port 11434 must never become the LAN/Tailscale interface. The stated long-term
  operator goal is private LAN/Tailscale availability through a separately reviewed,
  authenticated TLS gateway with explicit access policy and no public-web exposure.
- OCR/images remain a named V1 coverage gap, not an implicitly approved feature.

Operator-run handoff:
- On 2026-08-17 the operator started the first private, normal desktop run over a
  310-file extraction inventory. At handoff it was still running with a healthy lease
  and available resource schedule. This is a time-bound observation, not a terminal
  result; query durable summaries again rather than trusting the old counts.
- Do not open source documents, raw model output, grounded findings, or generated report
  content unless the user explicitly authorizes that inspection. Content-free run,
  lease, stage, count, manifest-hash, and integrity checks are in scope for diagnosis.
- Do not cancel, resume, abandon, or otherwise mutate the live run unless requested.

First actions:
1. Run `git status --short` and confirm the worktree is clean.
2. Read the current run through `experimental.analyst.service.list_run_summaries()` and
   owner-only sidecar metadata. If it is complete, verify its manifest/integrity through
   production verification APIs without reading report findings.
3. Report any operational discrepancy with durable evidence before changing code.
4. Ask the operator to choose and freeze exactly one Phase 2 objective. Recommended
   sequencing is: close the first operator-run acceptance first; then decide between the
   authenticated private-network gateway, OCR/image coverage, or measured workflow/
   performance work.
5. Write a new card in this `phase_2/` directory before implementation. Keep Phase 1
   cards and frozen benchmark evidence immutable.

Standing engineering constraints:
- Runtime entrypoint is only `./dirracuda`; `gui/main.py` is a legacy shim.
- Preserve GUI -> CLI boundaries for core scans and the reviewed detached Analyst worker
  architecture.
- Use `get_paths()` for canonical user-data paths.
- Never expose private content in logs, reprs, exceptions, tests, prompts, or handoff
  documents.
- Keep Ollama request identity, two-attempt semantics, resource scheduling, fencing,
  sandbox, parser provenance, and report-manifest checks fail-closed.
- Do not change dependencies, primary DB migrations, auth, CI, push, or branch state
  without the authorization required by AGENTS.md.
- Preserve unrelated user work. Use apply_patch for content edits and run focused plus
  proportional regression tests.

Recent validation evidence:
- Shared Analyst: 1,419 passed, 1,074 deselected.
- GUI baseline before the final adaptive-cadence tweak: 2,026 passed.
- Adaptive Analyst refresh focused gate: 11 passed; an unrelated Sherlock/Tk timing
  test failed once in the broad run and passed immediately in isolation.

Do not infer that candidate Phase 2 ideas are approved. Lead with the observed current
state and obtain the operator's scope decision.
```
