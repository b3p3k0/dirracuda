# Analyst — Read-First Analytical Contract

- Date: 2026-09-18
- Status: **drafted for freeze.** Supersedes the analytical core of `../CONTRACT.md`.
- Rule: `../CONTRACT.md` stays frozen and unedited. This file records supersession, the same
  way errata never edit the frozen contract.

This is the authoritative analytical spec that cards R1+ implement against. Changing anything
here after the R0 review PASS needs a new review, not a card-level decision.

---

## 1. What changes and why

Today's Analyst detects, then asks the model to confirm each regex hit as one of four fixed
tags. The model never reads the host, never rates risk, never names an owner. On the operator's
`test_1` run it reported a lawnmower-manual phone number and could not rank by value.

The redesign flips the pipeline to **read, then verify**. The model reads the host's files and
writes a senior-tech read. Detectors and a facts extractor gather grounded values. The facts
are ranked so noise sinks. The report leads with the read and backs it with facts.

---

## 2. Supersede / keep map

Superseded in `../CONTRACT.md`:

| Section | Was | Becomes |
|---|---|---|
| §2 Pipeline | detect -> confirm | **read -> verify** (§4 here) |
| §7 Model worksheet | one four-tag per-chunk classifier | two prompts: a host-read prompt and a facts-extractor prompt (§5, §6 here) |
| §11 Report artifacts | coverage-first HTML, four artifacts | `report.json` source of truth; read-first render (§8 here) |
| §14 UI surfaces | low-input launcher, U4 picker | simplified launch + Advanced dialog + two-layer report view + batch export (`UI_CONTRACT.md`) |

Kept from `../CONTRACT.md` (unchanged):

- §3 Execution model: detached worker, GUI -> service -> `python -m experimental.analyst.worker`
  boundary, one GPU lease, heartbeat, fence, reconciliation.
- §3.2 SQLite concurrency (writer model B) and E13 rollback-journal mode.
- §4 Coverage vocabulary and the one-terminal-per-file invariant. Coverage still computed; it
  moves from the top of the report into a "Files read" line plus a details view.
- §5 Parser sandbox (bubblewrap) and every parser limit. E9 read-only data handoff.
- §6 Parser selection by format. E10 PDF build, E11 OOXML gates, E12 `.xls` parser.
- §7 Grounding rule: every grounded fact quotes an exact substring; model character offsets are
  not trusted; the aggregator locates the quote itself (C0B-1: models score 0.0% on offsets).
- §8 Ollama request contract: loopback-only, redirects off, proxies ignored, cloud-tag
  rejection, streamed cancellation, per-request and total-run deadlines, charged contact ledger.
  One item relaxes: the single pinned tag becomes a per-run recorded selection (§7 here).
- §9 Resume identity / version invalidation. New prompts get new pinned SHAs (R76 pattern).
- §10 Security posture (shipped parsers).
- §11 storage safety that is not the report shape: owner-only 0700 dirs / 0600 files, atomic
  temp+rename, canonical evidence kept unmodified, CSV formula-injection guard, static HTML CSP,
  content-free logs, symlink-safe containment. One item relaxes: read-time integrity
  (§9 here).
- §12, §13 source identity and extraction-manifest handoff.
- §15, §16 repository guardrails and integration points.

No safety invariant is removed. Only three relaxations are approved (§7, §9, and the later
remote card).

---

## 3. The two-layer report

Every report has two layers, clearly separated so a model guess is never read as a grounded
value.

**Layer 1 — the read (model judgment, labeled unverified):**

- One short plain-language paragraph: what the host appears to be.
- Likely owner and identity.
- Contact points.
- A risk rating: HIGH / MED / LOW.
- The top 3 to 5 exposures, named in English, worst first.

**Layer 2 — the facts (grounded, nothing invented):**

- The specific sensitive values.
- Each with a verbatim quote and the file it came from.
- Each with a rank so noise sinks to the bottom.

The read leads. The facts back it up.

---

## 4. The read-first pipeline

```
inventory -> extract text (sandboxed) -> gather facts (detectors + facts extractor, grounded)
          -> read (map-reduce over per-file summaries) -> rank facts -> finalize report.json
```

Two concurrency domains stay as in §2 of the frozen contract: CPU extraction and detectors,
then serial GPU model work. The model order flips: it forms the read, it does not gate on
detector hits. Detectors and the facts extractor feed the facts layer only.

Grounding is unchanged: a fact must quote an exact substring of the source; the aggregator
locates the quote by containment; ungrounded facts are dropped and counted.

---

## 5. The read (Layer 1 detail)

### 5.1 Fields

The read holds: `host_summary` (one paragraph), `likely_owner`, `contacts` (list),
`risk_level` (HIGH / MED / LOW), `top_exposures` (3 to 5 ranked English lines). Exact JSON in
`REPORT_JSON_SCHEMA.md`.

### 5.2 Owner attribution stays clearly unverified

The read is a model judgment and can be wrong. Wording (frozen; also in `UI_CONTRACT.md`):

- The read block header carries the line: **"Model's read - not verified. Facts below are
  grounded."**
- Owner and contacts are prefixed with **"Likely"** (e.g. `Likely owner:`).

No owner label is presented as fact. No read field triggers an action.

### 5.3 Risk rubric (HIGH / MED / LOW)

Simple and documented. The facts rank is grounded; the read's risk is the model's labeled
judgment, expected to agree with the rubric.

| Rating | Rule |
|---|---|
| HIGH | Grounded government-ID (SSN, passport) or financial-account (card, bank/routing, IBAN) values are present. |
| MED | Contact/PII clusters, or tax/financial documents, without any HIGH grounded identifier. |
| LOW | Incidental or no sensitive identifiers. Noise only (e.g. a helpline number in a manual). |

The rubric leans on grounded facts, so the risk line is defensible even when the model's prose
is off. A HIGH grounded fact forces at least HIGH risk regardless of model wording.

### 5.4 How the read forms without blowing context

Large hosts overflow any single prompt (the real corpus is ~116k files, ~71% RTF). Strategy
(frozen): **map-reduce.**

1. Map: each supported file gets a compact, bounded summary — its grounded facts plus a
   one-line gist. No raw document body is carried forward.
2. Reduce: a bounded reduce pass reads the summaries, writes the host read, and ranks
   exposures worst-first.

Rejected: single-pass (overflows context on large hosts); sampled-skim alone (misses value).
Quick look may sample the map inputs (§7); Full read maps every supported file.

Per-request `num_ctx` / `num_predict` bounds, temperature 0, thinking disabled where supported,
the untrusted-nonce fence, per-chunk isolation, and the two-attempt budget all stay.

---

## 6. The facts (Layer 2 detail)

### 6.1 Reuse the grounding rigor

The facts extractor reuses the proven grounding machinery, not the four-tag classifier:

- The evidence shape (quote + located offset), NFC de-duplication, and the span-fraction guard
  from the current worksheet grounding.
- The untrusted-nonce fence and per-chunk isolation.
- Strict schema validation, retry once, then a counted invalid terminal.

The four-tag classification prompt is retired. The facts prompt is new, so it gets a new pinned
prompt/schema SHA. Drift fails closed (R76 pattern preserved).

### 6.2 Detectors stay, facts-layer only

Deterministic detectors (SSN, card, routing, IBAN, email, phone, DOB, bank account, passport,
demographic terms) still run over extracted text, bounded. They add cheap grounded identifiers
that regex is good at. They no longer select or gate model work.

### 6.3 Fact ranking

Each grounded fact carries a rank so noise sinks. Rank leans on kind: HIGH for government-ID
and financial-account values; low for incidental contact matches (the lawnmower phone). Ranking
is deterministic and documented alongside the rubric in `REPORT_JSON_SCHEMA.md`. A low-ranked
fact never reaches Top Exposures.

---

## 7. Quick look vs Full read, and model selection

### 7.1 Quick vs Full

- **Quick look** (default): read a representative sample of the host's files for a fast verdict.
- **Full read**: read every supported file.

Both produce the same two-layer report shape. Quick states in the read that it sampled, and how
many files it read of how many.

### 7.2 Model pin relaxes (approved relaxation 2)

The user picks a model from a dropdown the server reports. The chosen model tag and its resolved
digest are recorded per run (the schema already has `analyst_runs.model_tag` and `model_digest`).
Every report says what produced it.

Touch points for the code card: `ollama_contract.MODEL_TAG`, `models.ANALYST_DEFAULTS`, and the
module assert coupling them. The pin becomes a per-run recorded selection, not a hard constant.

### 7.3 Model list without an uncharged contact

Lesson 161 and R94: the tab must make no uncharged Ollama contact, and every contact is
precharged in the ledger. Frozen resolution:

- The Advanced "Refresh" is an **explicit, user-initiated, charged control contact**, recorded
  in `analyst_ollama_contacts` like any other control contact. It is never an on-open probe.
- `/api/tags` already exists in `ollama_client.check_tags`. Broaden `parse_tags_response` to
  return the full local, non-cloud model list. Cloud-tag rejection (`:cloud`, `-cloud`) stays.
- No `/api/show` is added. Digest provenance stays from `/api/tags`, recorded per run.

Endpoint stays literal-loopback-only. No raw port 11434 LAN/Tailscale exposure. Remote is a
later, separately reviewed card.

---

## 8. report.json and the sidecar read tables

- `report.json` is the source of truth, one per run. It holds the read and the ranked facts and
  carries `report_schema_version` (start at 1).
- Readable formats render from that JSON on demand: **Markdown (primary)**, plain text, HTML.
- The read also persists in the `analyst.db` sidecar as schema v4 tables (queryable, so the
  Runs list shows risk without opening each file). D5-shaped: host-keyed as the primary protocol
  tables key it, additive, no cross-DB join, so a later promotion into `dirracuda.db` is a clean
  lift. Migration reuses the `db_schema.py` guardrails (idempotent init in one `BEGIN
  IMMEDIATE`; refuse foreign/partial DBs).
- Export writes `.md` / `.json` / `.txt` / `.csv` anywhere. Copy sends the whole report to the
  clipboard. Batch export handles many selected runs at once.
- The canonical grounded evidence still lives in the 0600 evidence file, unmodified. HTML and
  CSV stay derived, escaped display copies under the frozen CSP and CSV formula guard.

Full schema in `REPORT_JSON_SCHEMA.md`.

### 8.1 Re-run everything

The new report view requires `report.json`. A run without it (every pre-redesign run, including
`test_1`) is shown legacy and is re-run under the new pipeline to get a read. There is no
in-place migration of old outputs and no compatibility view.

---

## 9. Integrity relaxes to warn-not-block (approved relaxation 1)

Today a moved or changed report folder fails closed and will not open. That is why `test_1`
would not open. Frozen change:

- On open, still recompute the on-disk manifest hash.
- On mismatch, **open the report with a "changed since saved" badge** instead of raising.
- Never present a changed report as verified.

Touch points for the code card: `report.verify_completed_report` and the ownership/permission
raises in `report_writer.inspect_report_manifest` / `_require_safe_existing` / `_inspect_artifact`,
plus the caller `service.completed_report_html`.

What does not relax: owner-only 0700 dirs and 0600 files at write time, atomic temp+rename,
symlink-safe containment, and canonical evidence kept unmodified. Only the read-time block
becomes a warning.

---

## 10. What stays fail-closed

Everything except the three approved relaxations (integrity read-time, model pin, and the later
remote card). Named explicitly:

- Bubblewrap sandbox and all parser limits. Sandbox unavailable -> preflight fails.
- Worker lease, heartbeat, fence, and reconciliation.
- Two-attempt semantic budget; charged contact ledger; resource backoff/pause.
- Loopback-only Ollama endpoint; redirects off; proxies ignored; cloud-tag rejection.
- Prompt/schema SHA pinning with drift fail-closed.
- No private content in logs, reprs, exceptions, tests, prompts, or docs.
- The model takes no action. Its output is report data only.

---

## 11. Open questions resolved (brief section 13)

1. Quick vs Full read — §7.1.
2. Host read without context overflow — §5.4 (map-reduce).
3. Risk rubric — §5.3.
4. Worksheet reuse for the facts extractor — §6.1.
5. Owner-attribution wording — §5.2.
6. `report.json` versioning and migration — §8, §8.1.

---

## 12. Card sequence

See `R0_CARD.md`. R1 pure models/prompts/report.json builder; R2 sidecar v4; R3 read-first
worker; R4 model dropdown; R5 render + warn-not-block; R6 launch UI; R7 report view + export +
copy + batch; R8 closeout. Remote model server is a later, separate gate.

---

## Amendment A1 (2026-09-18, HI-approved) — reuse worksheet-v2 as the facts map

Supersedes the parts of §4 and §6.1 that said to retire the four-tag prompt and introduce a
new per-chunk FACTS prompt. Reason: the existing worksheet-v2 per-chunk engine already extracts
grounded facts in the same four categories with verbatim quotes and provenance, plus a per-chunk
`document_type` and `subject` (a <=160-char summary). It lives inside the safety-critical C11
engine (charged contacts, fence, two-attempt budget, cancellation, resume, R92 request
identity) guarded by a hard `analyst_chunks` CHECK. Rewiring the per-chunk prompt there is high
churn/risk for a small semantic gain.

Decision:
- The **per-chunk map keeps worksheet-v2 unchanged** as the grounded facts + per-chunk-summary
  producer. No change to `_dispatch_chunk`, `finish_valid_attempt`, `analyst_chunks`, request
  identity, or the C11 content tests.
- Read-first adds three things on top: a **host READ reduce** (new host-level model call over the
  per-chunk summaries + a facts digest), **deterministic fact ranking**, and **`report.json`**
  (+ `analyst_read` / `analyst_read_exposures`).
- The R1 `read_worksheet` READ prompt is used for the reduce. The R1 FACTS prompt is unused for
  now (kept in the module; may be removed at closeout). §6.1 grounding reuse still holds via
  worksheet-v2's existing grounding.

All other read-first contract points (two-layer report, ranking rubric §5.3, owner wording
§5.2, report.json source of truth §8, warn-not-block §9, fail-closed §10) are unchanged.
