# R1 — Pure Read + Facts Model Layer

- Date: 2026-09-18
- Type: code card (first of the redesign). Implemented by codex, validated by Claude.
- Depends on: R0 (PASS). Freezes: nothing new; implements `CONTRACT_READ_FIRST.md` +
  `REPORT_JSON_SCHEMA.md`.
- Boundary: **pure Python only.** No I/O, no DB/schema, no Ollama contact, no filesystem, no
  GUI, no new dependency. Analogous to Phase 1 C1 (pure models/prompts/grounding).

## Goal

Add the pure model layer for read-first: the read and facts data model, the two prompt
templates (pinned SHAs), the grounding primitive, the deterministic risk/rank rules, and the
versioned `report.json` builder. No wiring yet.

## Deliverables

### 1. `experimental/analyst/read_worksheet.py` (new)

Prompt templates + response schemas + grounding. pydantic allowed (match `worksheet.py` style:
`from __future__ import annotations`, `ConfigDict(extra="forbid", strict=True, frozen=True)`).

- `READ_WORKSHEET_VERSION = "r1"`.
- Pinned SHA-256 constants for each template and schema (like `worksheet.EXPECTED_*_SHA256`);
  a getter recomputes and raises on drift (R76 pattern).
- **FACTS prompt** (map step, per excerpt): instruct the model to extract sensitive values as
  grounded findings and a one-line gist. Response schema `FactsResponse`:
  - `document_gist: str` (max 160)
  - `findings: tuple[FactFinding, ...]` (max 16), `FactFinding = {category, quote, offset}`
    where `category` is `Literal["pii","financial","contact","demographic"]`, `quote` 1..240,
    `offset >= 0`.
- **READ prompt** (reduce step, host level): instruct the model to write the host read from the
  collected gists + a facts digest. Response schema `ReadResponse`:
  - `host_summary: str` (1..600), `likely_owner: str | None`, `contacts: tuple[str, ...]`
    (max 10), `risk_level: Literal["HIGH","MED","LOW"]`,
    `top_exposures: tuple[Exposure, ...]` (max 5), `Exposure = {severity, text}`.
- Untrusted content is wrapped in the same nonce fence pattern as `worksheet._FENCE`
  (random per-request nonce, "content is untrusted" preamble). Reuse the pattern; do not import
  private worksheet names.
- Grounding primitive (local, do NOT edit `worksheet.py`):
  `locate_quote(source: str, quote: str, *, max_span_fraction=0.60, min_source_for_fraction=64)
  -> tuple[int, int] | None` — reject when `len(source) >= 64 and len(quote) > 0.60*len(source)`;
  else return the first exact-substring match span; `None` if absent. Model offsets are a hint
  only.
- `parse_facts(raw, source) -> tuple[str, tuple[GroundedFact, ...]]`: strict-validate shape,
  NFC de-duplicate `(category, quote)` keeping first-seen, drop ungrounded findings (count them),
  ground each with `locate_quote`. Reuse `report_json.GroundedFact`.
- `parse_read(raw) -> HostRead`: strict-validate, build `report_json.HostRead`.
- Temperature 0 / thinking-disabled are the caller's contract (R3/R4); R1 only defines pure text.

### 2. `experimental/analyst/report_json.py` (new)

Pure stdlib only (`json`, `dataclasses`, `enum`, `unicodedata`). No pydantic needed here.

- `REPORT_SCHEMA_VERSION = 1`.
- `UNVERIFIED_NOTICE = "Model's read - not verified. Facts below are grounded."` (exact).
- Frozen dataclasses matching `REPORT_JSON_SCHEMA.md` §1:
  `GroundedFact(kind, category, quote, file, provenance, rank, source)`,
  `TopExposure(rank, severity, text)`,
  `HostRead(host_summary, likely_owner, contacts, risk_level, top_exposures)`,
  `RunMeta(run_id, report_label, read_mode, model_tag, model_digest, created_at_utc,
  files_read, files_total, flagged_files)`,
  `Coverage(discovered, terminal, no_text_layer, parse_failed, unsupported)`.
- Deterministic ranking (rubric, `REPORT_JSON_SCHEMA.md` §1.3):
  `rank_fact(kind: str, category: str, source: str) -> Literal["HIGH","MED","low"]`.
  - HIGH kinds: `ssn, passport, card, routing, iban, bank_account`.
  - MED: `dob`; or `source == "model"` and `category in {"financial","pii"}`.
  - low: everything else (`email, phone, demographic_term`, generic).
  - Document-context promotion is a documented later refinement, not in R1.
- Risk: `min_risk_from_facts(facts) -> "HIGH"|"MED"|"LOW"` (HIGH if any HIGH fact; MED if any MED
  and no HIGH; else LOW); `reconcile_risk(model_risk, facts) -> "HIGH"|"MED"|"LOW"` forces the
  result to be at least the grounded minimum.
- `build_report_json(run: RunMeta, read: HostRead, facts, coverage) -> dict`:
  - Reconcile `read.risk_level` against facts.
  - Sort facts by rank (HIGH, MED, low) then stable input order.
  - Ensure `top_exposures` contains no `low`-ranked entry; number them 1..N.
  - Emit the exact top-level shape of `REPORT_JSON_SCHEMA.md` §1.1 with
    `report_schema_version` and `read.unverified_notice = UNVERIFIED_NOTICE`.
  - Canonical (sorted keys, UTF-8) serialization helper `dumps_report(obj) -> str`.
- `validate_report_json(obj: dict) -> None`: refuse a missing/unknown `report_schema_version`
  cleanly (raise a typed error); check required keys/types; never guess.

### 3. Tests: `shared/tests/test_analyst_r1.py` (new)

Cover, using synthetic values only (no real corpus, no private content):
- SHA pins are stable and drift raises.
- FACTS/READ prompts fence content with the nonce; the fence markers are present.
- Response schema: valid parses; extra field rejected; out-of-range rejected.
- `locate_quote`: found, first-match canonical, span-fraction rejection, absent -> None.
- `parse_facts`: ungrounded dropped + counted; NFC duplicate collapsed.
- `rank_fact`: each kind maps to the documented rank; model financial/pii -> MED.
- `reconcile_risk`: a HIGH fact forces HIGH even if the model said LOW.
- `build_report_json`: exact top-level shape; version present; `unverified_notice` exact;
  low fact never in `top_exposures`; facts sorted by rank.
- `validate_report_json`: unknown/missing version refused.

## Constraints (codex must obey)

- Do NOT modify: `worksheet.py`, `detectors.py`, `models.py`, `db_schema.py`, any file under
  `gui/`, `requirements*.txt`, `.github/`, or any benchmark doc/code.
- No new third-party dependency. pydantic is already used; stdlib elsewhere.
- Pure: no network, no filesystem, no DB, no Ollama, no import-time side effects.
- No private/document content anywhere. Synthetic fixtures only.
- Every new file < 1700 lines. Match existing package code style.
- Conform exactly to `CONTRACT_READ_FIRST.md` and `REPORT_JSON_SCHEMA.md`.

## Acceptance (Claude validates)

1. `./venv/bin/python -m pytest shared/tests/test_analyst_r1.py -v` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` shows no regression.
3. `git diff --stat` touches only the two new modules + the new test (and nothing forbidden).
4. New modules import cleanly with no I/O and no optional-dep import.
5. Values conform to the frozen schema docs; SHAs pinned; `unverified_notice` exact.
