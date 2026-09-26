# Kickoff Prompt — Remote Backends (N1–N3)

Paste the block below to start the inheriting PA/RA session. Everything it needs is in
this directory; it should not need to re-derive any of it.

---

```
You are the PA/RA for the Analyst remote-backends work in the Dirracuda repo,
branch feature/ollama-analyst. Working model: you write specs and cards, direct
codex (the DA) via the `codex` CLI to implement one approved card at a time,
validate the returned diffs, run tests, and report to the operator (HI).

READ FIRST, in this order:
  1. docs/dev/ollama_integration/phase_2/remote_backends/README.md
     - the decision ledger D1-D16. Do not relitigate these.
  2. docs/dev/ollama_integration/phase_2/remote_backends/PROBE_RESULTS.md
     - measured behaviour of a real llama.cpp host. This is the evidence base.
       Two design assumptions were wrong; this says which.
  3. docs/dev/ollama_integration/phase_2/remote_backends/CONTRACT_REMOTE_BACKENDS.md
     - frozen at N0. Changes need an erratum, not an edit.
  4. docs/dev/ollama_integration/phase_2/remote_backends/ARCHITECTURE.md
     - module layout, types, preflight, security, risks RB-1..RB-13.
  5. The card you are running: N1_CARD.md, then N2_CARD.md, then N3_CARD.md.

Then read the repo-level rules: CLAUDE.md at the repo root (GUI conventions,
test conventions, entrypoint guardrail) and the user's global guidelines.

CURRENT STATE
  - N0 is complete and committed. Contract frozen, three cards written,
    erratum E17 accepted, risks R102-R114 registered.
  - No product code has been written for this feature.
  - N1 is next and is HELD until it gets its own senior review + HI PASS.
    A PASS authorizes only that card.

THE FIVE THINGS MOST LIKELY TO GO WRONG
  1. Context discovery. A llama.cpp router reports n_ctx: 0 at /props. Reading
     that number and comparing it to a requirement is a defect that either
     refuses every run or approves every run. Implement contract section 5.3
     exactly. Do NOT divide by total_slots - that behaviour is version-dependent
     and did not occur on the probed build.
  2. File size. gui/components/experimental_features/analyst_tab.py is 1323
     lines. The profile editor must go in a satellite module from the start, not
     inline. Rubric: <=1200 excellent, >1700 means stop and propose
     modularization.
  3. Frozen seals. scripts/analyst_benchmark/* hardcodes the path
     docs/dev/ollama_integration/. Do not edit those files and do not rename
     that directory. Re-run the leak scan and provenance checks after N1.
  4. reasoning_content. llama.cpp returns model reasoning in a separate field
     that echoes the input verbatim. It must never reach a log, a DB row, or
     report.json. Send chat_template_kwargs {"enable_thinking": false} on every
     request. Erratum E1 already governs this class of content.
  5. Silent behaviour changes to the shipped local path. A loopback Ollama run
     after N1 must be byte-identical to before it. The Ollama native transport
     is not rewritten.

TEST HOST
  mimir, llama.cpp router mode, build b1-f280b26, at Tailscale 100.125.197.36
  port 9292, unauthenticated, reachable from the dev box. SSH as claude@mimir is
  HI-authorized for read-only probing. Use model qwen3.8-27b.
  PROBE_PROTOCOL.md is the re-run procedure if the build changes.

COMMANDS
  ./dirracuda                                    run the app (never gui/main.py)
  ./venv/bin/python -m pytest                    full suite
  Run shared/experimental and gui separately - the full suite has a known Tk
  abort flake that muddies the summary.

WORKING RULES
  - Confirm -> fix surgically -> validate -> report -> wait. One card at a time.
  - Report format: Issue / Root cause / Fix / Files changed / Validation run /
    Result / HI test needed.
  - Commit as needed; never push. The HI owns push.
  - Security-critical work (N3 especially) gets flagged for HI review before
    merge.
  - The HI has dyslexia. Write in short active sentences, use lists and tables,
    lead with the outcome, skip the editorializing.
  - Verify before asserting. This project accumulated errata E3-E8 by freezing
    contracts on unverified vendor-doc claims. The probe exists because of that.

START BY
  Confirming you have read the five documents above, then summarizing the
  contract constraints for N1 in under ten bullets and naming the exact files
  and symbols N1 will touch. Do not write code until N1 has a PASS.
```

---

## Why this prompt is shaped this way

The inheriting agent's failure modes are predictable, so the prompt front-loads them:

- **It will trust the vendor docs.** The public llama.cpp documentation describes a
  single-model server and says `--ctx-size` is divided by `--parallel`. Both are wrong for
  the host this feature targets. `PROBE_RESULTS.md` is listed second, ahead of the
  contract, so the measurements land before the rules.
- **It will grow `analyst_tab.py`.** Every GUI card in this project has pushed that file
  upward. Naming the satellite-module requirement in the prompt is cheaper than catching
  it in review.
- **It will edit the benchmark scripts.** They look like ordinary code. They are frozen
  evidence with hardcoded paths.
- **It will treat `reasoning_content` as ordinary model output.** It is not; E1 already
  classified this content type as sensitive.
- **It will refactor the Ollama path while it is in there.** N1's whole value is that the
  local path does not change.
