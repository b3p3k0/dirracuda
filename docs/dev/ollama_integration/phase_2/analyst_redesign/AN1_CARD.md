# AN1 — Reliable, intelligent host READ (real analysis)

- Type: analytical code card. codex implements, Claude validates (synthetic host + real model).
- Depends on: nothing new. AN2 (excerpts) builds on this input. Operator decisions: model reads the
  full inventory (+ excerpts in AN2); model drives risk/exposures with a grounded floor.

## Goal
Make Layer 1 (the host read) actually run and actually analyze: reliably parse across local models,
see the whole host (filenames + salient facts), reason semantically (owner/subject, noteworthy
files by name and meaning), and drive risk + top exposures (grounded floor kept). No silent fallback.

## Deliverables

### 1. Read reliability (`read_worksheet.py`, `read_reduce.py`, `ollama_contract.py`)
- `parse_read`: tolerate real-model output. Extract the first balanced JSON object from
  `response.content` (strip leading/trailing prose or reasoning/think), DROP unknown keys before
  validation (so `extra="forbid"` doesn't trip on `reasoning`/`notes`), coerce obvious types, then
  validate + normalize (clamp `host_summary` to max, dedupe/cap `contacts`, cap `top_exposures` to 5,
  drop empty text). Keep a validated result shape.
- Add a REPAIR pass: a `build_read_repair_prompt` (+ pinned hash) that says "return ONLY the JSON
  object, no prose". `read_reduce` uses attempt 1 = primary, attempt 2 = repair (not a blind retry).
- READ-specific generation profile (do NOT reuse the frozen worksheet options): add
  `READ_NUM_CTX=16384`, `READ_NUM_PREDICT=2048`, `READ_MAX_SOURCE_CHARS` (~24000) used by
  `build_read_chat_request` / the read source bound. These are tunable; document them. (worksheet
  per-chunk options stay frozen.)
- New pinned `EXPECTED_READ_*` SHAs for the new prompt/schema/repair.

### 2. Rich read input (`read_reduce._load_reduce_input` / `_render_summary`)
- FULL file inventory: for every `analyst_files` row emit `relative_path` (filename) + `format_name`
  + `terminal_code` + a per-file signal (detector categories/kinds present, model-reviewed?,
  finding count). Bounded: cap to N (e.g. 400) lines, list flagged files first, summarize the tail
  ("+K more files").
- Salient grounded facts WITH values: expand beyond 16 to a bounded set that includes contacts and
  high-value categories (file + category + value), so the model can identify the owner and elevate
  buried high-value facts. Bounded to the READ source cap.
- Keep the coverage counts. Whole blob bounded to `READ_MAX_SOURCE_CHARS`.

### 3. Semantic analyst prompt (`_READ_INSTRUCTIONS` rewrite)
Instruct the model to: (a) state what the host appears to be; (b) identify the likely owner/subject
and contact points from the material; (c) call out noteworthy files/content BY NAME and BY MEANING
(credentials, genetic/medical, a dependent's or minor's data, identity documents, financial), even
without a regex hit; (d) rate risk by real sensitivity; (e) list top exposures worst-first, and it
MAY include semantic items not tagged by regex. Keep: unverified labeling, untrusted-nonce fence,
output-is-data, no invented values. Raise `MAX_HOST_SUMMARY_CHARS` (e.g. 600 -> 1200).

### 4. Model-driven risk + exposures, grounded floor (`report_json.py`, `report_state.py`)
- Risk = max(model risk, `min_risk_from_facts`) — model may RAISE, floor prevents under-rating.
- Do NOT drop the model's top exposures for being non-regex; keep grounded floor semantics.
  Revisit `reconcile_risk` (keep floor) and the LOW-exposure drop / "canonical ranks" so a model
  exposure the model deemed noteworthy survives. report.json shape unchanged (no version bump).

## Constraints
- worksheet-v2 per-chunk FACTS map unchanged (Amendment A1). Loopback-only; excerpts are AN2.
  Content-free logs/reprs. Files: read_worksheet.py, read_reduce.py, ollama_contract.py,
  report_json.py, report_state.py, tests. Files < 1700 lines.

## Acceptance (Claude validates)
1. Unit/component: tolerant parse (extra keys, wrapped prose, think -> parses); repair path used on
   first failure; inventory + salient facts appear in the rendered read input incl. all filenames;
   risk = max(model, floor); a model exposure is not dropped.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` and
   `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` => 0 failed.
3. Real-model smoke (Claude, loopback, synthetic planted host + an installed model like qwen3:14b):
   the READ succeeds (analyst_read row written, non-empty summary + owner + >=1 exposure), no
   fallback; a credentials-named file is referenced. Validate STRUCTURE only, not private values.
