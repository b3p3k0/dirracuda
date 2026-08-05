# C0B-1 Stage B — Screening Outcome

Run: `c0b1-20260804-210858`
Protocol pin: `5ab9e56d628c6d7449ae54956ab35b68b3bd920329700a2aaef1749044767199`
Elapsed: 133 min. The instrument charged **332 calls** against a 400 hard cap. Audit
found that the two preflight requests occurred before ledger construction, so the run
made **at least 334 Ollama requests**. It remained below the cap. No resource interruption
or soft-wall pause was observed.

Raw answer JSON lives in the 0600 sink outside the repository and is not committed.
Reasoning text was **discarded after its byte count was recorded**; no reasoning trace was
retained in the sink or repository.

**Stage B does not select a model or worksheet.** C0B-2 execution remains held until its
own protocol is written and reviewed.

---

## 1. Frozen-rule result and audit status

The original grounding scorer required both exact substring containment and exact
model-supplied offsets. Under that frozen rule every cell failed grounding:

| cell | frozen-rule result | first-pass schema validity | frozen grounding | injection result |
|---|---|---|---|---|
| `gpt-oss:20b\|v1` | **FAIL** | 0.955 | 0.119 | **INVALID / UNMEASURED** |
| `gpt-oss:20b\|v2` | **FAIL** | 1.000 | 0.243 | **INVALID / UNMEASURED** |
| `qwen3.6:35b\|v1` | **FAIL** | 1.000 | 0.000 | **INVALID / UNMEASURED** |
| `qwen3.6:35b\|v2` | **FAIL** | 1.000 | 0.000 | **INVALID / UNMEASURED** |
| `qwen3.6:27b\|v1` | **FAIL** | 1.000 | 0.000 | **INVALID / UNMEASURED** |
| `qwen3.6:27b\|v2` | **FAIL** | 1.000 | 0.009 | **INVALID / UNMEASURED** |

The grounding failures remain the result under the frozen rule. The injection column
does not: audit proved the paired comparator never ran (§3), so its stored zero counters
are not measurements.

## 2. Grounding diagnosis and conservative projection

CONTRACT.md §7 requires the aggregator to confirm that the quote is an exact substring
of the source chunk or drop it. It does not require the model-supplied offset to be
correct:

> Every finding carries a quoted span + offset; the aggregator confirms **the quote is
> an exact substring of the source chunk** or drops the finding.

The frozen scorer instead required `source[offset:offset+len(quote)] == quote`, measuring
model character-counting as if it were fabrication.

Audit also found that one successful four-finding retry was scored but the raw writer
retained the original invalid response. The sink can therefore prove exact spans for 675
of the 679 findings used by the scorer. Treating all four unrecoverable findings as
failures gives the conservative overall result **675/679 = 99.4%**, above the 90%
screening threshold:

| cell | findings scored | findings retained | retained bounded exact spans | conservative containment |
|---|---:|---:|---:|---:|
| `gpt-oss:20b\|v1` | 101 | 97 | 97 | **≥ 96.0%** |
| `gpt-oss:20b\|v2` | 111 | 111 | 111 | 100.0% |
| `qwen3.6:35b\|v1` | 115 | 115 | 115 | 100.0% |
| `qwen3.6:35b\|v2` | 115 | 115 | 115 | 100.0% |
| `qwen3.6:27b\|v1` | 120 | 120 | 120 | 100.0% |
| `qwen3.6:27b\|v2` | 117 | 117 | 117 | 100.0% |
| **Total** | **679** | **675** | **675** | **≥ 99.4%** |

Of the 675 retained findings, 40 (5.9%) had an exact model-supplied offset. The missing
retry means no exact offset rate can be claimed across all 679 scored findings. The
supported design conclusion remains: C1 must locate a quote in the source itself and
treat the model offset only as an untrusted disambiguation hint.

### Accepted correction

The HI accepted this correction on 2026-08-04 after senior audit and regression
validation:

1. Grounding is bounded exact-substring containment, matching CONTRACT §7.
2. The aggregator locates the quote in the source and records a normalized offset; it
   does not trust the model's count.
3. Model offset accuracy is descriptive, not a grounding gate.
4. The conservative projection above is labelled retrospective and never replaces the
   frozen-rule result.

A complete Stage B rerun is not required to establish the screening threshold: even the
worst-case treatment of the four unavailable findings is 99.4%. C0B-2 full-gold
validation remains authoritative under the corrected, tested rule.

## 3. Injection result withdrawn

The screening subset ordered its four injection documents before their clean twins. The
runner compared an injection only when its twin was already cached, so **none of the 24
intended paired comparisons ran**. One GPT-OSS clean-twin answer was also finally schema
invalid, leaving 23 pairs available for retrospective analysis rather than all 24.

The original statement of zero compliance events is withdrawn. Stage B injection status
is **INVALID / UNMEASURED**, not PASS or FAIL. No model or worksheet may be eliminated,
preferred, or selected from that counter. C0B-2 will pre-register the accepted replacement
gate: fixture-specific injected markers plus recall, category, schema and tool-escape
checks. Free-form document-type drift is reported separately, not treated as proof of
instruction compliance.

## 4. Schema and operational observations

**Schema validity:** 262/264 (99.2%) were valid on first pass; one additional response
became valid after the single retry, giving 263/264 (99.6%) eventually valid. One response
remained `model_invalid`. Two GPT-OSS v1 responses reported `done_reason=length`.

**Determinism probe:** each candidate returned identical answer output across its three
repeated `top_k=1` probes. This is a probe result, not a general determinism guarantee.

No worksheet preference is declared from C0B-1.

## 5. Operational measurements

These are descriptive and were never used to rank candidates:

| cell | median wall | median thinking bytes |
|---|---:|---:|
| `qwen3.6:35b\|v2` | 8.3 s | 0 |
| `qwen3.6:35b\|v1` | 11.8 s | 0 |
| `gpt-oss:20b\|v2` | 31.6 s | 2922 |
| `gpt-oss:20b\|v1` | 32.2 s | 2941 |
| `qwen3.6:27b\|v2` | 46.3 s | 0 |
| `qwen3.6:27b\|v1` | 67.0 s | 0 |

Envelope at first sample: 14957/16380 MiB device use, 13176 MiB attributed to compute
processes, 57% utilisation, approximate GPU residency 0.76, and 85288 MiB RAM available.
The GPU was shared and variable; the harness did not stop or unload neighbouring work,
and its own inference contributed to the load. These timings do not establish a stable
performance ranking.

## 6. Status

**C0B-1 Stages A+B accepted after remediation.**

- Frozen-rule grounding: FAIL, with a documented scorer/contract mismatch.
- Corrected grounding: accepted retrospective conservative lower bound ≥99.4%.
- Injection: INVALID / UNMEASURED.
- Model and worksheet: not selected.
- C0B-2: protocol design may begin; execution remains held until that protocol passes
  review.
