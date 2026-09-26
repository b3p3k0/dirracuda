# C0B-2 Public C/D/F Outcome

Date: 2026-08-08
Run: `c0b2-20260808-142358-305902662babf91d485138fb`
Source commit: `5c154b5`
Result: **`INCONCLUSIVE/no_d3_context_survivor`**

This is the public-only terminal record for the canonical C0B-2 run. It contains
aggregate decisions and counters only. No raw model answer, reasoning text, prompt,
private document fact or private identifier is included.

## What happened

| Stage | Frozen result |
|---|---|
| C | `gpt-oss:20b` failed both worksheets. Both Qwen models passed v1 and v2; the frozen engineering-default rule selected v2. |
| D1 | Both Qwen candidates passed every tested output budget. The smallest passing value, `num_predict=1024`, continued. |
| D2 | `qwen3.6:35b` missed one of 12 boundary identifiers at every chunk size and stopped. `qwen3.6:27b` passed all three sizes; the largest passing value, `chunk_chars=8000` with `overlap=256`, continued. |
| D3 | `qwen3.6:27b` passed the 16,384-context runtime probe and every quality check except the zero-false-positive gate. One negative document produced a false positive. |
| F | Not activated. There was no final Stage-D survivor. |
| E | Not eligible and not run. No private corpus was accessed. |

The D3 terminal reason names the absence of a context survivor, but context capacity was
not the problem. Ollama reported the requested 16,384 allocation, and both 8,192 and
16,384 met measured input/output fit. The candidate failed the strict negative-control
quality gate. Ollama documents allocated context in the
[`/api/ps` response](https://docs.ollama.com/api/ps) and the memory tradeoff of larger
contexts in its [context-length guide](https://docs.ollama.com/context-length).

## D3 evidence

| Metric | Result |
|---|---:|
| Completed eventual-valid chunks | 66 / 66 |
| Raw findings grounded | 131 / 131 |
| Retained findings grounded | 131 / 131 |
| Category recall | 6 / 6 in each of four categories |
| Boundary identifiers retained | 12 / 12 documents |
| Schema, length or headroom failures | 0 |
| Negative false-positive documents | **1** |
| 16,384 context probe | PASS |

The frozen rule requires zero negative false-positive documents. The single miss therefore
eliminated the last candidate. No D4 plan was created.

## Ledger and integrity

| Counter | Value |
|---|---:|
| Charged calls | 530 |
| Preflight/control calls | 13 |
| Primary scored calls | 514 |
| Schema retries | 3 |
| Accepted attempts | 525 |
| Schema-invalid attempts | 5 |
| Successful work items | 512 |
| Completed-invalid work items | 2 |

Stage C sealed a verified boundary receipt at 275 calls. The terminal receipt was then
created at 530 calls. Final verification returned `ok=true` with no errors; SQLite
integrity passed with no foreign-key violations.

Two earlier canonical checkpoints remain as receipted `FAILED_SAFETY` history. Each
stopped during `/api/show` preflight before scored work. E3 and E4 corrected those
bounded compatibility findings prospectively; neither failed run was reclassified.

## Disposition

No final model/worksheet/config bundle or product default was selected. Stage C's
per-candidate worksheet choices and the Stage-D factor decisions are intermediate
evidence only. The contract's D1 (primary model) and D2 (chunk/context) decisions remain
unresolved. Stage F cannot run from this terminal, and private Stage E is not merely
waiting for authorization—it lacks the required public selection parent.

The next attempt needs a separate reviewed card and a fresh source-pinned checkpoint.
Changing candidates, prompts, worksheets or quality rules after this result would be a
new experiment, not a continuation or rescore of this one.

## Validation commands

```bash
./venv/bin/python -m scripts.analyst_benchmark c0b2 status \
  --run-id c0b2-20260808-142358-305902662babf91d485138fb
./venv/bin/python -m scripts.analyst_benchmark c0b2 verify \
  --run-id c0b2-20260808-142358-305902662babf91d485138fb
```

Both commands are read-only. `status` reports terminal `INCONCLUSIVE`; `verify` reports
`ok=true` and an empty error list.
