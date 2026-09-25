# report.json Schema and Sidecar v4 Read Tables

- Date: 2026-09-18
- Status: drafted for freeze (R0). R2 finalizes exact DDL against this shape.
- Source of truth: `report.json` on disk. The sidecar tables are a queryable projection.

---

## 1. report.json — the source of truth

One file per run. Written atomically (temp + rename), owner-only 0600, alongside the canonical
grounded evidence file. It carries the read (Layer 1) and the ranked facts (Layer 2).

### 1.1 Top-level shape

```json
{
  "report_schema_version": 1,
  "run": {
    "run_id": "…",
    "report_label": "host12",
    "read_mode": "quick",
    "model_tag": "qwen3.6:27b",
    "model_digest": "a50eda8ed977…",
    "created_at_utc": "2026-09-18T14:22:00Z",
    "files_read": 310,
    "files_total": 332,
    "flagged_files": 22
  },
  "read": {
    "unverified_notice": "Model's read - not verified. Facts below are grounded.",
    "host_summary": "Small-business accounting server. Appears to belong to Anytown Tax & Books LLC. Holds client tax returns and payroll with SSNs and bank details.",
    "likely_owner": "Anytown Tax & Books LLC",
    "contacts": ["office@anytowntax.example", "(555) 123-4567"],
    "risk_level": "HIGH",
    "top_exposures": [
      {"rank": 1, "severity": "HIGH", "text": "Client SSNs in 2023_returns.xlsx (48 rows)"},
      {"rank": 2, "severity": "HIGH", "text": "Payroll bank accounts in payroll_q3.csv"},
      {"rank": 3, "severity": "MED",  "text": "Owner personal cell in contacts.vcf"}
    ]
  },
  "facts": [
    {
      "kind": "ssn",
      "category": "pii",
      "quote": "123-45-6789",
      "file": "2023_returns.xlsx",
      "provenance": "sheet 2, row 14",
      "rank": "HIGH",
      "source": "detector",
      "occurrences": 48,
      "plausibility": "valid"
    },
    {
      "kind": "phone",
      "category": "contact",
      "quote": "(555) 890-1212",
      "file": "mower_manual.pdf",
      "provenance": "p.7",
      "rank": "low",
      "source": "detector",
      "occurrences": 1,
      "plausibility": "valid"
    }
  ],
  "coverage": {
    "discovered": 332,
    "terminal": 332,
    "no_text_layer": 9,
    "parse_failed": 3,
    "unsupported": 8
  }
}
```

### 1.2 Field rules

- `report_schema_version` — integer, starts at 1; currently **3**. A reader that does not know a
  version refuses the file cleanly (no guessing). Readers accept `(1, 2, 3)`: v1 predates the
  model-identity kind (erratum E18), v2 predates the collapsed fact (amendment A3), and both
  keep opening with the newer fields defaulted.
- `run.read_mode` — `quick` or `full`.
- `run.model_tag` / `run.model_digest` — the per-run recorded selection (§7.2 of the contract).
  Digest is lowercase 64-hex.
- `read.unverified_notice` — the exact frozen string. Always present.
- `read.likely_owner`, `read.contacts` — model judgment. The UI prefixes them "Likely".
- `read.risk_level` — `HIGH` / `MED` / `LOW`. Forced to at least HIGH when any grounded fact
  ranks HIGH (rubric, contract §5.3).
- `read.top_exposures` — 3 to 5 entries, worst first, each with `rank` (1-based), `severity`
  (`HIGH`/`MED`/`LOW`), and English `text`. No low-ranked fact appears here.
- `facts[]` — bounded to the highest-ranked facts (cap `MAX_REPORT_JSON_FACTS`, e.g. 500) so
  report.json stays small even for a 46k-file host. The FULL grounded evidence remains in
  `findings.jsonl` (canonical). Each entry is grounded only. Each carries a verbatim `quote`, its `file`, human `provenance`,
  a `rank` (`HIGH`/`MED`/`low`), and `source` (`detector` or `model`). Every quote is an exact
  substring of its source (contract §6.1). `category` reuses the existing detector categories.
- **One entry per `(source, kind, category, quote, file)`** (amendment A3). Repeated occurrences
  of one value in one file fold together; they differed only in `provenance` and spent the cap.
  The fold happens before the cap is applied, and the first occurrence's `provenance` is kept.
- `facts[].occurrences` — integer >= 1, v3+. How many times this value occurs in this file.
- `facts[].plausibility` — `valid` / `suspect` / `public`, v3+. What the value's own form says
  about it under published allocation rules. v3 writes only `valid`; the other two are reserved
  for the identifier-screening work. A screened-down fact is ranked lower and labelled, never
  silently removed — coverage honesty is the product (frozen §4).
- `coverage` — the honest counts. Coverage stays computed (frozen §4); it moves out of the
  report's lead into a "Files read" line plus a details view.

### 1.3 Fact ranking (deterministic)

Rank is derived, not invented by the model:

| Rank | Rule |
|---|---|
| HIGH | Government-ID (`ssn`, `passport`) or financial-account (`card`, `routing`, `iban`, `bank_account`) grounded value. |
| MED | Other PII/financial context, or `dob`, or a model-sourced grounded finding in a financial/PII document. |
| low | Incidental contact matches (`email`, `phone`) and `demographic_term` with no supporting HIGH/MED context. |

The lawnmower phone lands `low` and never reaches `top_exposures`.

---

## 2. Sidecar schema v4 — read tables

Purpose: let the Runs list show risk without opening each `report.json`, and make the read
queryable for future cross-run work. `report.json` stays the source of truth; these tables are a
projection written at finalize.

D5 discipline (frozen): host is keyed the way the primary protocol tables key it (via
`analyst_runs`, which already carries `host_type`, `ip_address`, `port`, `protocol_server_id`);
tables are additive; no cross-DB join; a later promotion into `dirracuda.db` is a clean lift.

Migration reuses the existing `db_schema.py` guardrails: bump `SCHEMA_VERSION` 3 -> 4;
`KNOWN_SCHEMA_VERSIONS = (1, 2, 3, 4)`; idempotent init inside one `BEGIN IMMEDIATE`; upgrade
v1/v2/v3 -> v4 additively; refuse foreign or partial databases without mutation.

### 2.1 `analyst_read` — one row per run

```sql
CREATE TABLE analyst_read (
  run_id                TEXT PRIMARY KEY REFERENCES analyst_runs(run_id),
  report_schema_version INTEGER NOT NULL CHECK(report_schema_version >= 1),
  read_mode             TEXT NOT NULL CHECK(read_mode IN ('quick','full')),
  risk_level            TEXT NOT NULL CHECK(risk_level IN ('HIGH','MED','LOW')),
  host_summary          TEXT NOT NULL,
  likely_owner          TEXT,
  contacts_json         TEXT NOT NULL,          -- canonical JSON array of strings
  files_read            INTEGER NOT NULL CHECK(files_read >= 0),
  files_total           INTEGER NOT NULL CHECK(files_total >= 0),
  flagged_files         INTEGER NOT NULL CHECK(flagged_files >= 0),
  created_at_utc        TEXT NOT NULL
) STRICT;
```

### 2.2 `analyst_read_exposures` — ranked top exposures

```sql
CREATE TABLE analyst_read_exposures (
  run_id     TEXT NOT NULL REFERENCES analyst_runs(run_id),
  ordinal    INTEGER NOT NULL CHECK(ordinal >= 1),      -- 1..5, worst first
  severity   TEXT NOT NULL CHECK(severity IN ('HIGH','MED','LOW')),
  text       TEXT NOT NULL,
  PRIMARY KEY (run_id, ordinal)
) STRICT;
```

### 2.3 Ranked facts

The authoritative ranked facts live in `report.json`. They are already grounded in the existing
`analyst_detector_hits` and `analyst_model_findings` tables. To make rank queryable, R2 adds one
additive nullable column to each:

```sql
ALTER TABLE analyst_detector_hits  ADD COLUMN fact_rank TEXT
  CHECK(fact_rank IS NULL OR fact_rank IN ('HIGH','MED','low'));
ALTER TABLE analyst_model_findings ADD COLUMN fact_rank TEXT
  CHECK(fact_rank IS NULL OR fact_rank IN ('HIGH','MED','low'));
```

No finding row is otherwise changed. No new facts table is introduced in v4; a dedicated facts
projection is deferred until a cross-run query needs it.

### 2.4 Surfaced in the Runs list

`service.list_run_summaries` and `AnalystRunSummary` gain `risk_level` (from `analyst_read`) so
the Runs "Result" column can show `● HIGH risk` or, for an active run, percent read. The read
is written at finalize, so an in-progress run has no `analyst_read` row and shows progress
instead.

---

## 3. Versioning and migration

- `report.json` version: `report_schema_version`, integer, starts at 1. Bump on any incompatible
  shape change. Readers refuse unknown versions cleanly.
- Sidecar version: schema v4. Guardrailed migration as in §2.
- Re-run everything: a run with no `report.json` and no `analyst_read` row is legacy. It is shown
  legacy and re-run under the new pipeline. No in-place migration of old outputs.
