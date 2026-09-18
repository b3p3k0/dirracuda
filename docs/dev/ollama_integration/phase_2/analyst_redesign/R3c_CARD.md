# R3c — Finalize: report.json (source of truth) + fallback read + ranking

- Date: 2026-09-18
- Type: code card. codex implements, Claude validates.
- Depends on: R1 (report_json builder), R3a/R3b (analyst_read persisted). Completes the worker
  pipeline: after this card a run produces report.json end to end.

## Goal

At finalize, write `report.json` as the source of truth: the host read (from analyst_read, or a
deterministic fallback) over the ranked grounded facts. Keep `findings.jsonl` as the full
canonical evidence. Do not break the existing atomic-writer / manifest safety.

## Deliverables

1. **report_json.py (pure)**: add
   `build_fallback_read(facts, *, files_read, files_total, flagged_files) -> HostRead` — used when
   no model read exists. risk_level = `min_risk_from_facts`; `host_summary` a bounded generic line
   (e.g. "Automated read unavailable. N files reviewed, M flagged."); `likely_owner=None`;
   `contacts=()`; `top_exposures` = up to 5 highest-ranked non-low facts rendered as English
   (e.g. "SSN in returns.xlsx"). Pure, deterministic.
2. **report_contract.py**: insert `"report.json"` into `REPORT_ARTIFACT_NAMES` in sorted position
   -> `("findings.csv","findings.jsonl","report.html","report.json","run.json")`. Add
   `MAX_REPORT_JSON_FACTS = 500`.
3. **Adapter** (new function; put DB reads in report_state.py or a new report_read.py, NOT in the
   pure report_json.py): read `analyst_read` + `analyst_read_exposures` (-> HostRead) and the
   top-ranked grounded facts (detector hits + model findings joined to file + provenance),
   compute each fact's rank with `report_json.rank_fact`, keep the top `MAX_REPORT_JSON_FACTS` by
   rank, and assemble RunMeta (from the snapshot's ReportRun: run_id, report_label,
   mode->read_mode mapping fast->quick/deep->full, model_tag, model_digest, created_at_utc; files_*
   from coverage: files_total=discovered, files_read=model-reviewed count, flagged_files=files with
   >=1 grounded finding) and Coverage (from CoverageSummary). Then
   `report_json.build_report_json(...)` -> dict; if no analyst_read row, use `build_fallback_read`.
4. **report_writer.py**: add `_render_report_json` and publish `"report.json"` via
   `SecureReportDirectory.publish` (already gated by REPORT_ARTIFACT_NAMES). Write the validated
   canonical bytes from `report_json.dumps_report`. `findings.jsonl` stays the full evidence;
   `report.html`/`findings.csv`/`run.json` stay for now (R5 revisits HTML).
5. **report.py finalize_report**: build the report.json payload (via the adapter) and pass it to
   the publisher so `_render_report_json` can write it; the manifest now covers 5 artifacts and its
   SHA changes (expected). Everything else (begin/finish finalization, heartbeat, atomic writes,
   symlink safety, owner-only modes) unchanged.
6. **Tests**: update the C12 manifest/artifact tests (5 artifacts, new ordered manifest, byte
   stability) WITHOUT weakening the symlink/tamper/atomic safety assertions. Add
   `shared/tests/test_analyst_r3c.py`: a finalized run's report.json validates via
   `report_json.validate_report_json`; the model read is used when analyst_read exists; the
   deterministic fallback is used when it is absent; facts are bounded to MAX_REPORT_JSON_FACTS and
   sorted by rank; findings.jsonl still holds the full evidence.

## Constraints

- report_json.py stays pure (no DB/IO). DB reads live in report_state.py / report_read.py.
- Do NOT weaken C12 finalization safety (atomic temp+rename, O_EXCL/O_NOFOLLOW, owner-only modes,
  symlink refusal, no-complete-on-writer-failure, content-free). The manifest/artifact-set change
  is the only expected C12 test movement.
- No schema change (analyst_read/exposures/fact_rank already exist from v4). fact_rank columns may
  stay unpopulated (rank computed on the fly); do not mass-update them.
- Files < 1700 lines. Match style. No raw doc content beyond stored bounded quotes/subjects.

## Acceptance (Claude validates, real venv)

1. `./venv/bin/python -m pytest shared/tests/test_analyst_r3c.py -q` passes.
2. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
3. A finalized run writes report.json that `report_json.validate_report_json` accepts; findings.jsonl
   still present and full; manifest covers 5 artifacts and verifies.
4. `git diff` shows report_json.py still pure and C12 safety assertions intact.
