# Analyst — Frozen Implementation Contract (C0A)

Date: 2026-08-04
Status: **Frozen.** Architecture approved across three senior reviews + a final
contract review (PASS). This document is the authoritative spec that cards C1+
implement against. Changing anything here after C0A review requires a new review,
not a card-level decision.

Scope of C0A: documentation only. No runtime dependency file and no CI
configuration is changed in C0A; those need their own HI-approved cards.

---

## 1. What Analyst is

An offline document-review tool ("digital intern"). It reads documents Dirracuda
already extracted from open directories, finds priority exposure (PII,
financial/tax, contact, demographic), and writes a standardized **per-host**
report with honest coverage accounting. It runs against the local Ollama stack
with no required cloud egress. It is an **optional** experimental feature — the
core GUI must start and run without any of Analyst's dependencies installed.

Platform: **Linux-only for V1** (depends on `resource`/`RLIMIT_*`, signals,
bubblewrap). Preflight gates this.

## 2. Pipeline shape

```
inventory -> extract text (sandboxed) -> detect (deterministic, all files)
          -> select -> model worksheet (flagged files) -> aggregate -> report
```

Two phases, two concurrency domains:
- **Phase 1** (CPU, parallel): extract + deterministic detectors over 100% of
  supported files. Establishes coverage.
- **Phase 2** (GPU, serial): model worksheet on Phase-1-selected files only (Fast
  mode) or all supported files (Deep mode).

Two tracks feed the aggregator: deterministic detectors own structured
identifiers; the model owns classification, unstructured findings, and prose.
Neither does the other's job.

## 3. Execution model

Analysis runs as a **dedicated detached worker subprocess**
(`python -m experimental.analyst.worker --run-id <id>`), not in the GUI process
and not through the core GUI→CLI scan boundary.

- The worker survives GUI closure; SQLite is the single source of durable
  progress/resume state.
- Detached launch: exact venv Python, repo cwd, `shell=False`, closed inherited
  fds, `start_new_session`, stdout/stderr to a controlled non-sensitive log
  (never GUI-owned pipes).
- Parser children use an explicit `spawn`/`forkserver` start context — never
  implicit `fork` from the multithreaded Tk process.

### 3.1 Worker lease, heartbeat, reattachment (protects the single GPU)

- **One active-run lease, global to the GPU** (not per-run-id), claimed
  atomically in SQLite.
- Persist worker PID **plus process-start identity** (start time / boot-id
  token), heartbeat timestamp, lease owner. PID alone is untrusted (OS reuse).
- GUI startup reconciles: reattach to a live worker (validate PID +
  start-identity + fresh heartbeat) or mark the run `interrupted`/resumable and
  clear the stale lease. Running Tasks is hydrated from the Analyst DB (the
  in-memory registry does not survive restart).
- Two GUI instances launching two different run-ids: exactly one acquires the GPU
  lease.
- Cancellation signals validate PID + process-start identity before signalling.

### 3.2 SQLite concurrency (writer model B)

The worker is the only **steady-state** writer. The service performs **short
serialized control transactions** for launch / cancel / lease-claim /
reconciliation. Contract: WAL mode; defined `busy_timeout`; short transactions;
bounded retry on transient locks; atomic lease/run claims; crash recovery that
never strands an "active" run. The phrase is "one concurrent writer
transaction," not "only one process may ever write." Reference:
https://sqlite.org/wal.html

## 4. Coverage vocabulary (state machine)

Coverage honesty is the product. "Detector-scanned" is not "semantically
analyzed."

**Stages (progress, not outcomes):** `discovered` → `format_identified` →
`text_extracted` → `detector_scanned` → `selected_for_model` → `model_reviewed`
→ `model_response_valid`.

**Successful terminals:** `complete_detector_only`, `complete_model_reviewed`,
`complete_no_supported_content`.

**Failure/skip terminals (stable reason codes; no generic `skipped`):**
`unsupported_format`, `no_text_layer`, `parse_timeout`, `parse_oom`,
`parse_signal`, `parse_error`, `parser_output_limit`, `oversize`, `empty`,
`encrypted`, `sandbox_unavailable`, `sandbox_error`, `model_invalid`,
`model_timeout`, `model_transport_error`, `source_changed_since_inventory`,
`cancelled_abandoned`, `skipped_analyst_output`, `skipped_known_bad`.

**Nonterminal, resumable:** `cancelled_pending_resume` (NOT in the terminal
list).

Rules:
- Report finalize requires **exactly one terminal per discovered file.**
- Fast mode reports per-stage percentages, e.g. "100% detector-scanned; 18%
  model-reviewed." Detector-only files are `complete_detector_only`, never
  "analyzed, no findings."
- Coverage is the first section of every report.

### 4.1 Cancellation / resume / finalization

- Crash/interruption → resumable nonterminal work; no complete report finalized.
- Normal cancellation → stops new work, pending/in-flight files become
  `cancelled_pending_resume`; the partial checkpoint counts them incomplete and
  is **never labelled a complete report.**
- Resume processes pending files without reopening immutable successful terminals.
- Explicit abandon → remaining work becomes `cancelled_abandoned` (terminal); run
  is non-resumable.
- Only a complete or explicitly-abandoned run satisfies the
  one-terminal-per-file invariant.

## 5. Parser sandbox (bubblewrap) — a security boundary, not just resource limits

The shipped PDF parser (PyMuPDF/MuPDF) has RCE-class history (§10). Parsers
process hostile input, so every parser runs inside bubblewrap.

**Filesystem / input handoff (TOCTOU-safe):**
- Open the input `O_NOFOLLOW`, `fstat` it, pass the already-open fd via
  `--ro-bind-fd`. The fd reaches the subprocess only through `pass_fds`; unrelated
  descriptors stay closed; the fd stays open until bubblewrap finishes bind/setup.
- Mount only the current user-data file (read-only) plus an explicit read-only
  runtime allowlist (venv Python, parse-worker + runtime files, parser binary,
  shared libs, fonts) — **not the whole repository** — and a private synthetic FS.
- **In-place-modification defense:** fingerprint the source from that same fd;
  after parsing, re-check the fd (size/metadata/hash) or copy into a bounded
  private snapshot before parsing. `O_NOFOLLOW` + fd-bind stop path replacement,
  not another process mutating the same inode. On any post-parse difference,
  discard the result and record `source_changed_since_inventory`.
- `--clearenv`, then add only required vars; private writable tmpdir; empty
  private HOME. Never expose real HOME, source dir, `/run/user`, host D-Bus, SSH
  agents, or unrelated `/etc` secrets.
- `--unshare-net`, `--unshare-pid`, `--cap-drop ALL`, `--die-with-parent`;
  process-group isolation + guaranteed cleanup.
- LibreOffice path (optional): private profile; macros, external-link handling,
  Java, and update behavior disabled.

**Resource limits:** applied via a single-purpose launcher / `prlimit`, **not
Python `preexec_fn`** (the worker may be threaded). Limit address space, CPU,
process count (`RLIMIT_NPROC`), open FDs (`RLIMIT_NOFILE`), core dumps
(`RLIMIT_CORE`). PID namespaces alone do not stop fork bombs.

**IPC bounds (beyond `RLIMIT_FSIZE`, which does not bound piped output):** cap
extracted text bytes/chars, IPC response size, parser stdout/stderr, per-document
page/sheet/member counts, and aggregate expanded archive/OOXML size. On
overflow/timeout, kill the whole **process group**, record
`parser_output_limit`/`parse_timeout`, wipe the private tmpdir.

**Guardrail:** native parser libraries are never imported into the durable
worker; parser imports happen only inside the sandboxed one-file child.

**Fallback (locked):** sandbox unavailable → **preflight fails.** A
reduced-isolation mode is deliberately hard to misuse: per-run, non-persistent
acknowledgement; **unavailable to the automatic post-extract hook**; labelled as
running the parser under the user's normal OS account; recorded in run/report
metadata; **prohibited for the private benchmark and formal acceptance tests.**

**Residual boundary (stated honestly):** bubblewrap constrains filesystem,
process, capability, and network access; it is not protection against an
exploitable host-kernel vulnerability, and its security depends entirely on the
supplied policy. Reference: https://github.com/containers/bubblewrap

## 6. Parser selection by format

Route by **sniffed magic bytes**, not extension. All parsers run in the §5
sandbox.

| Format | Parser | Notes |
|--------|--------|-------|
| RTF | RTF text stripper | ~61% of the enumerated V1 candidate-document corpus; trivial, text-only |
| txt / xml | direct text | encoding detection |
| PDF | **PyMuPDF** (pinned) | pdfplumber fallback for table-heavy pages; no text layer → `no_text_layer` |
| DOCX/XLSX/PPTX | OOXML + `defusedxml` | member-count / per-member size / aggregate size / compression-ratio / path gates before any XML parse |
| `.doc` | **antiword** | installed; lightest dep |
| `.xls` | **xlrd** or sandboxed LibreOffice | benchmarked in C7; xlrd reads historical `.xls` only, ignores macros, returns cached formula results (not formula text) — "parsed" ≠ full workbook semantics |

**Never invoke `catdoc` or `xls2csv`** (TALOS-2024-2132 heap corruption), even
sandboxed — a safer route exists. `olefile` is container inspection only, not a
cell extractor. No approved parser → `unsupported_format`.

## 7. Detectors and model worksheet

**Deterministic detectors** (pure, no I/O): regex + checksum (Luhn for cards,
mod-97 for IBAN) for SSN, cards, bank/routing, phone, email, DOB, passport, etc.
Run over 100% of extracted text. These own all identifier counts.

**Model worksheet** (Phase 2): a fixed, versioned JSON schema answered per chunk.
Classification (document type, subject), sensitive-category presence with quoted
evidence, and an `insufficient_evidence` enum value so uncertainty has a
structured home instead of leaking into prose.

- Temperature 0. Thinking disabled. No tools, images, or capabilities.
- Every finding carries a quoted span + offset; the aggregator confirms the quote
  is an exact substring of the source chunk or **drops the finding.**
- Pydantic-validate every response; retry once; then record `model_invalid`.
- Chunking has a defined overlap; findings are de-duplicated across chunk
  boundaries. Provenance: PDF page, spreadsheet sheet/cell, document
  paragraph/section where available, plus normalized chunk offset.

## 8. Ollama request contract (deterministic; enumerate, do not abbreviate)

Exact loopback URL validation (no DNS-derived hosts); redirects disabled; ambient
proxies ignored; selected tag resolved to and checked against its digest;
structured-output schema supplied; bounded `num_ctx` and `num_predict`;
temperature 0; thinking disabled where supported; no tools/images/external
capabilities; bounded response bytes and parsed-object size; per-read,
per-request, and total-run deadlines; prompt/worksheet/model-digest stored with
every result.

**Local-only wording (default contract, verbatim):** "Analyst connects only to a
literal-loopback Ollama endpoint, disables redirects, ignores ambient proxies,
rejects known cloud tag forms (`:cloud` and `-cloud`), and runs only a locally
installed model whose tag and digest match the approved benchmark. Server-level
egress control is an operator prerequisite Analyst cannot prove." Tag filtering
is defense-in-depth, not proof of locality. No "zero cloud egress" claim anywhere.
Optional strong contract (operator upgrade, documented not enforced): disable
Ollama cloud + verify server config/logs + OS/container egress boundary.

**Cancellation:** a `threading.Event` cannot interrupt a blocking `stream:false`
read — use `stream:true` + a cancel-checked read loop + socket deadlines.
`/api/ps` lists loaded models (skewed by `keep_alive`), so it does not prove a
request stopped. Acceptance test (§ C9) verifies prompt client close, prompt
worker cancellation record, a following short request within a bound, and that
the total-run deadline still stops the client if server inference continues; if
server termination is unproven, report "cancel requested; server completion
unverified." Sources: https://docs.ollama.com/faq ·
https://docs.ollama.com/api/streaming · https://docs.ollama.com/api/ps

## 9. Resume identity / version invalidation

At run start store: model tag + resolved digest (via `/api/tags`, verified
against the benchmarked digest); prompt/worksheet/schema version; detector rules
version; parser implementation + dependency versions (incl. embedded MuPDF);
Analyst selection/chunking settings; source fingerprint (resolved identity, size,
mtime, content hash). Before resume, compare; on any relevant change, reprocess
from the affected stage or fork a new run — never silently continue with a
different model behind the same mutable tag.

## 10. Security posture (shipped parsers)

- PyMuPDF/MuPDF: CVE-2026-3308 integer overflow → OOB heap write → possible RCE
  (fixed in MuPDF 1.28.0); CVE-2026-3029 path traversal in the embedded-file
  **CLI** extraction path (fixed in PyMuPDF 1.26.7) — ordinary imported text
  parsing need not exercise that path, but pin anyway.
- **Version pin (C0B selects exact):** an approved PyMuPDF bundling **MuPDF ≥
  1.28.0**; preflight asserts **both** `pymupdf_version` and the embedded
  `mupdf_version`. Package version alone is insufficient.
- OOXML: XXE via external entities can read local files / SSRF — `defusedxml`,
  no external entities, no network; plus the zip-bomb gates in §5/§6.
- catdoc/xls2csv: TALOS-2024-2132 — not used.

Sources: https://mupdf.com/releases/cve ·
https://www.cve.org/CVERecord?id=CVE-2026-3029 ·
https://www.talosintelligence.com/vulnerability_reports/TALOS-2024-2132

## 11. Storage, output, and report safety

- Sidecar SQLite at `~/.dirracuda/data/experimental/analyst.db` (Dorkbook/
  Keymaster precedent). Migration-ready for later `dirracuda.db` adoption: no
  cross-DB joins, host keyed as the primary protocol tables key it, additive
  columns.
- Raw values are stored (HI accepted the aggregation risk).
- Permissions: 0700 dirs, 0600 DB/report/log files. Atomic report replacement
  (temp + rename).
- **Canonical evidence** lives only in the 0600 JSONL, unmodified. HTML and CSV
  are derived, escaped/sanitized display copies, also 0600.
- CSV formula-injection guard: prefix-guard cells beginning with `= + - @`, tab,
  CR/LF, plus quotes/separators and Unicode/full-width equivalents. Sanitize only
  the CSV view; never mutate the canonical JSONL.
- Static HTML report: no JavaScript, no remote assets, all styling embedded;
  extracted URLs rendered as text unless explicitly safe; exact CSP:
  `default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; script-src
  'none'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action
  'none'`. Context-appropriate escaping; tests cover HTML/script tags, event
  handlers, data URLs, remote images, malformed links.
- Operational logs: bounded, rotated, content-free — no prompts, document text,
  model output, or identifiers in routine logs, exceptions, progress messages, or
  benchmark output.
- Symlink-safe output containment (static and symlink-swap tests). Retention/
  deletion controls, with a stated caveat that SQLite WAL and DB backups may
  retain deleted raw values.
- `_analyst/` output is excluded from future analysis runs.
- Scale: streamed CSV / JSONL + compact summary JSON; paginated/partitioned HTML;
  lazy/paginated GUI browsing; SQLite aggregation queries, never a full
  finding-graph load (the 46,724-doc host is the design target).

## 12. Source identity and output placement

Source modes:
- **Extraction manifest:** use the canonical protocol/host identity from the
  `extract_run_summaries` row (looked up by **row id**, never by
  `(ip, host_type, created_at)`).
- **Single-host directory:** ask/confirm the report label/host identity — no
  name-guessing (handles labelled dirs, IPv6, direct-host sources).
- **Multi-host root:** a defined directory convention.
- **Unknown:** a stable `unattributed-<hash>` source id.

Output defaults to `<source_dir>/_analyst/<host>/`; detect when the selected
directory is already the host directory to avoid `<host>/_analyst/<host>`.

## 13. Extraction-manifest handoff

`extract_run_summaries` already exists (`shared/db_migrations.py:570`, docstring
"portable analyst handoff"); `summary_json` carries per-file `saved_to` durable
paths (post-ClamAV routing, written at
`gui/utils/protocol_extract_runner.py:381,866`).

Contract (implemented in C14):
- The persistence API returns a structured reference
  (`{db_row_id, fallback_log_path, source}`); it stops encoding the row id inside
  a synthetic filename (`write_extract_log` currently returns
  `db_extract_run_summary_{id}.json`).
- Every extraction flow persists the manifest **before** offering Analyst and
  passes the exact identity in; the dashboard post-scan flow (currently
  unpersisted) is wired.
- Missing table / fallback JSON handled via runtime table+column guards, not
  schema assumption. No new schema columns unless genuinely required; any
  schema/migration change needs HI approval.

## 14. UI surfaces

- **Accessories → Analyst tab:** low-input launcher. Source (latest tmpfs extract
  / a directory); output dir; fixed qualified model identity; a fast pre-scan scope
  line (sniff only, no parse). C9B supersedes the early mockup's pre-run `/api/tags`
  refresh: every Ollama contact is durably precharged, so version/tag/digest and
  reachability are verified by the worker only after a run exists. C14 supplies the
  latest-extract manifest choice; C13 exposes standalone directories.
- **Advanced:** Fast/Deep depth, file-type toggles, size cap, parse timeout.
- **Progress:** the existing scan/probe/extract monitor + Running Tasks; two
  progress lines (detector, model); live per-stage coverage counters; Hide/Cancel.
- **Report browser:** paginated/lazy; coverage-first.
- **U4 selection dialog:** per-row checkboxes + select all/none over the model's
  recommended findings, exported to **CSV/JSONL** (report rows/findings — **not**
  original-document copies). Original-document copying is out of the MVP; the
  ClamAV clean/infected/unknown copy contract is deferred with it.
- **Post-extract hook:** opt-in toggle, off by default, keyed off the manifest
  row id; not available under reduced-isolation mode.

## 15. Repository guardrails (every card)

- Tk interaction on the UI thread via existing `after(...)`/dispatcher; no
  worker/monitor thread destroys dialogs or touches widgets.
- `safe_messagebox`, `ensure_dialog_focus`, named `SMBSeekTheme` styles only.
- Check every touched file before and after editing; if it is already above 1700
  lines or the change would push it above 1700, pause and propose modularization.
- Update lessons-learned for the sandbox, lease/recovery, and manifest identity.
- Review `README.md` at the end of every card.
- Full regression = `./venv/bin/python -m pytest` (incl. experimental + Web UI).
- Real corpus files and raw model output stay out of git, fixtures, logs, and
  committed reports.

## 16. Integration points (touch during implementation cards)

- `shared/config_store.py::EXPERIMENTAL_MODULES` — add `"analyst"`.
- `shared/path_service.py::DirracudaPaths` — add `analyst_db_file` (alongside
  `keymaster_db_file`); construct under the experimental data dir.
- SettingsManager experimental shard `~/.dirracuda/conf.d/experimental/analyst.json`.
- Config-store and path-service tests for the new module/path.
- Optional dependency lane: `experimental/analyst/requirements-analyst.txt`
  (exact pins: PyMuPDF, pdfplumber, defusedxml, xlrd). Core GUI startup works when
  absent: no optional parser imports at package/GUI-module import time; preflight
  reports missing deps cleanly; parser deps load only in the sandbox child.
- AGPL: PyMuPDF is AGPL. HI accepted the combined-work implication for the whole
  distributed project (Dirracuda ships a web UI → AGPL §13 network-disclosure
  attaches). Record attribution, licence text, and corresponding-source
  expectations, and update README/licence notices **when the dependency is
  actually introduced** — this does not auto-relicense Dirracuda's own GPL files.
  Sources: https://www.gnu.org/licenses/gpl-faq.html ·
  https://www.gnu.org/licenses/agpl-3.0.html

## 17. Card sequence

C0A (this doc) → C0B (gold set + benchmark) → C1 pure models/detectors/worksheet
→ C2 safe inventory + lease/reattach → C3 parser supervisor + bubblewrap → C4 RTF
+ text → C5 PDF → C6 OOXML → C7 legacy `.doc`/`.xls` → C8 sidecar schema + resume
state machine + concurrency → C9 Ollama client + cancellation/GPU spike → C10
Phase 1 orchestration → C11 Phase 2 orchestration → C12 streaming report/export →
C13 Accessories UI + Running Tasks hydration → C14 manifest identity + post-extract
hook → C15 scale + crash matrix + cancellation + docs + full regression.

Crash matrix (C8/C10/C11/C15): kill before claim / after claim / during parse /
during inference / during finalization — each resumes per §4.1.

**C0A acceptance:** (1) a partial checkpoint is never labelled a complete report;
(2) no runtime dependency file and no CI configuration changed during C0A.

## 18. Prerequisites (HI executes/approves; outside this repo)

- Ollama: disable cloud + confirm from config/logs; publish 11434 on host
  loopback only; optional egress boundary for the strong contract. DA proposes
  exact diffs; HI applies.
- Dependency approval before any `requirements.txt`/lane edit.
- CI: adding a bwrap smoke test / installing bwrap in CI needs HI approval.

## Card gate

C0B and every coding card are HELD until these C0A documents are reviewed.
