# C1 — Pure Analyst contracts

Date: 2026-08-14
Status: **Complete**

## Issue

C0B selected a model, worksheet and chunk envelope, but those decisions still live in
the benchmark harness. Production cards need one small, optional package that owns the
pure data contracts before any filesystem, database, parser, GUI or Ollama integration is
built.

## Root cause

The original C0B-1 prototype is no longer the complete contract. Later measured work
proved that model offsets are untrusted, duplicate evidence needs deterministic local
handling, and strict shape validation also needs explicit semantic checks. Blindly
copying the early three modules would restore defects already fixed in C0B.

## Scope

Create `experimental/analyst/` with:

- immutable domain models and the exact coverage vocabulary from `CONTRACT.md` §4;
- the recovered C0B-7 defaults: `qwen3.6:27b`, its selected digest, worksheet v2,
  8000/256-character chunks, `num_ctx=8192`, and `num_predict=1024`;
- pure overlapping chunking with absolute source offsets;
- deterministic detectors and checksum helpers, including the benchmarked detector
  subset plus context-labelled bank-account and passport identifiers;
- the selected worksheet-v2 JSON Schema and nonce-fenced prompt;
- strict worksheet parsing, semantic agreement checks, bounded duplicate-only local
  normalization, exact-substring grounding, and deterministic source-side quote
  location. A model offset is diagnostic only.

The early benchmark modules and frozen evidence remain unchanged.

## Guardrails

- No filesystem, database, subprocess, GUI, parser or network access.
- Importing `experimental.analyst` uses only the standard library. Pydantic remains
  isolated to the worksheet module so the optional feature cannot break core startup.
- No dependency, schema, migration, auth or CI file changes.
- No private document read and no model call.
- Extra fields, type coercion, overlong strings, more than 16 findings, invalid
  assessment/finding combinations and more than one redundant row fail closed.
- Ungrounded findings are counted and dropped; they never become report evidence.
- No blind retry contract is added. C9/C11 will own transport and any distinct repair
  request.

## Acceptance

1. Package import succeeds when Pydantic imports are blocked; worksheet import fails only
   when explicitly requested.
2. Coverage stages and terminal reason codes exactly match the frozen contract.
3. Chunk invariants hold for empty, boundary, overlap and invalid-parameter cases.
4. Detector tests cover valid and invalid checksums, near misses, ordering and labelled
   identifiers; the public gold set has no unexpected category misses.
5. Worksheet tests cover strict schema, prompt fencing, assessment agreement, duplicate
   normalization, ungrounded-drop behavior, repeated-quote location and untrusted model
   offsets.
6. Production files remain under 1200 lines.
7. Focused Analyst tests pass; wider regression runs only if the pure package exposes a
   cross-project failure.
8. Root `README.md` is reviewed. Because Analyst remains unreleased, it is changed only
   if current user-facing behavior changed.

## Sources and local authority

`CONTRACT.md`, `CONTRACT_ERRATA.md`, `PUBLIC_CDF_OUTCOME_C0B7.md`, `BENCHMARK.md` and
`LESSONS_LEARNED.md` are authoritative for behavior. Current Pydantic guidance supports
strict validation and `extra="forbid"`; current Python documentation supports immutable
frozen data classes and documents regex backtracking behavior. No new dependency is
introduced by this card.
