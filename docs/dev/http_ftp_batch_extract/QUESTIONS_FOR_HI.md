# Questions For HI

Date: 2026-04-01
Purpose: lock decisions before implementation.

## Q1) Probe prerequisite for FTP/HTTP bulk extract

Question:
Should FTP/HTTP bulk extract require a prior probe snapshot, or should extract perform a fresh remote enumeration when no snapshot exists?

Recommendation:
Require prior probe snapshot for V1. If missing, return `skipped` with a clear note.

Why:
Lowest-risk path, deterministic scope, and no new heavy remote enumeration path.

## Q2) Candidate scope depth

Question:
For FTP/HTTP V1 bulk extract, should we extract only sampled files present in probe snapshot (root + one-level directory files), or attempt deeper recursion?

Recommendation:
Snapshot-sampled files only (root + one-level) for V1.

Why:
Matches current probe model, predictable runtime, less regression risk.

## Q3) HTTP endpoint identity

Question:
When HTTP row lacks `port` in the selected target payload, should extraction resolve endpoint details from DB before cache lookup?

Recommendation:
Yes. Resolve `port/scheme` from DB detail first; fallback to row/defaults only if detail missing.

Why:
Avoid wrong cache path and endpoint drift.

## Q4) Missing snapshot outcome semantics

Question:
Should missing FTP/HTTP snapshot be `skipped` or `failed` in batch summaries?

Recommendation:
`skipped` with note: `Probe required before FTP/HTTP extract`.

Why:
This is an unmet precondition, not an operation failure.

## Q5) Detail popup fallback behavior

Question:
If detail popup extract callback is unavailable (fallback path), should FTP/HTTP be blocked with explicit message or supported there too?

Recommendation:
Explicitly block with clear message in fallback path, while normal app route (callback) supports all protocols.

Why:
Prevents accidental SMB-path calls in atypical contexts without broad popup refactor.

## Q6) Extension filters

Question:
Should FTP/HTTP bulk extract use the same `file_collection.included/excluded_extensions` and extension-mode semantics as SMB?

Recommendation:
Yes, unchanged semantics for parity.

Why:
Operator consistency and minimal new config surface.
