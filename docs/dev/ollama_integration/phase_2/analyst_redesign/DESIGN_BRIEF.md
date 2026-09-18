# Analyst Redesign — Design Brief

**Read-first host reports.**

- Date: 2026-09-18
- Status: **Objective frozen by the operator.** Not yet planned. No code authorized.
- Origin: AA (after-action) session on branch `feature/ollama-analyst`.
- Supersedes: the analytical core of the frozen Phase 1 `CONTRACT.md` (per-excerpt PII
  classification). The Phase 1 infrastructure stays.

This brief is the input to a planning (PA) session. The PA writes the plan and the
revised contract, then runs the senior-review gate before any code. See
[HANDOFF_PROMPT.md](HANDOFF_PROMPT.md).

---

## 1. Why we are doing this

The current Analyst does not read the host. It runs regex detectors, then asks the model
to confirm each regex hit as one of four fixed tags (`pii`, `financial`, `contact`,
`demographic`). The model never sees the whole host. It never rates risk. It never names
an owner.

Symptoms the operator hit on the real run (`test_1`, 310 files):

- It reported a phone number from a lawnmower manual. Useless.
- It did not sort by value. A private cell and a printed helpline both read as `contact`.
- The report answers "how many files scanned," not "what is this host and how bad is it."

The engine is elaborate. The analytical core is a per-chunk PII extractor with a model
rubber-stamp. That mismatch is the problem.

Source files that show this today:

- Prompt: `experimental/analyst/worksheet.py` (`_INSTRUCTIONS`, `WorksheetV2`).
- Regex catalog: `experimental/analyst/detectors.py` (9 patterns).
- Dialog: `gui/components/experimental_features/analyst_tab.py`.
- Report writer: `experimental/analyst/report_writer.py` (coverage-first HTML + CSV/JSONL).

---

## 2. What Analyst must produce (target)

A senior tech's read of a host's extracted files: "what is this, and what is interesting
here." Two layers.

**Layer 1 — The read (model judgment, labeled unverified):**

- One short plain-language paragraph: what the host appears to be.
- Likely owner and identity.
- Contact points.
- A risk rating (HIGH / MED / LOW).
- The top 3 to 5 exposures, named in English, worst first.

**Layer 2 — The facts (grounded, nothing invented):**

- The specific sensitive values.
- Each with a verbatim quote and the file it came from.
- Each with a rank so noise (the lawnmower phone) sinks to the bottom.

The read leads. The facts back it up. The two are clearly separated so a model guess is
never mistaken for a grounded value.

---

## 3. The pipeline flip

From "detect, then confirm" to **"read, then verify."**

1. The model reads the host's files and forms a host hypothesis (what it is, who owns it).
2. Detectors and the model gather grounded facts (exact sensitive values with quotes).
3. The model ranks the facts and writes the read, worst first.

Keep the regex detectors, but only in the facts layer. They no longer gate the model.
They add cheap grounded PII (SSNs, card numbers) that regex is good at.

**Grounding rule (unchanged from prior work):** a grounded fact must quote an exact
substring from the source. The model's character offsets are not trusted; the aggregator
locates the quote itself. This is a hard lesson from C0B-1 (models cannot produce reliable
offsets; 0.0% correct for both Qwen models).

---

## 4. Report storage and export

Operator priority: reports must be very exportable, always accessible, easy to copy.

- **`report.json` is the source of truth**, one per run. It holds the read and the facts.
- Render readable formats from that JSON on demand: **Markdown first**, plus plain text
  and HTML.
- The app shows the report from that data.
- **Export**: write `.md` / `.json` / `.txt` / `.csv` anywhere. **Copy**: whole report to
  clipboard.
- **Batch export**: select many runs, export all at once (see section 8).
- **Integrity: warn, do not block.** Keep a hash of the report. If the report folder moved
  or changed, show a "changed since saved" badge and still open it. Do not hide it.

Note: today the report is sealed and owner-only, and the app fails closed if the folder
changed. That is why the operator's `test_1` would not open. The redesign relaxes this
(approved, see section 10).

---

## 5. UI — Screen 1 (launch and runs)

One real input: the folder. Everything else is optional or under Advanced.

```
┌ Analyst ─────────────────────────────────────────────────────┐
│ Point Analyst at a folder of a host's extracted files.       │
│ It reads them and tells you what the host is and             │
│ what is worth your attention.                                │
│                                                              │
│ Folder  [ /home/kevin/Extracted/host12        ]  [ Browse ]  │
│ Name    [ host12                    ]   optional             │
│                                                              │
│ Read    (•) Quick look     ( ) Full read                     │
│                                                              │
│ [ Analyze ]                                     [ Advanced… ] │
│──────────────────────────────────────────────────────────── │
│ Runs                                        [ Select all ]   │
│ ┌─┬────────────┬────────┬───────────┬──────────────────────┐│
│ │☑│ host12     │ Quick  │ Running   │ 40% read             ││
│ │☑│ test_1     │ Quick  │ Done      │ ● HIGH risk          ││
│ └─┴────────────┴────────┴───────────┴──────────────────────┘│
│ [ Open report ]  [ Export selected… ]  [ Resume ]  [ Cancel ]│
│ Status: Reading host12...                                    │
└──────────────────────────────────────────────────────────────┘
```

Changes from today:

- One required input (the folder). Name auto-fills from the folder.
- Output path, saved-scan mode, model server, and engine info all move to Advanced.
- "Depth: Fast/Deep model-review deterministic hits" becomes "Read: Quick look / Full read."
- Runs column "Coverage" becomes "Result" (risk level or percent read).
- Runs support multi-select for batch export.

---

## 6. UI — Advanced (its own dialog)

A separate dialog, not a dropdown, so it has room to grow.

```
┌ Analyst — Advanced ────────────────────────────────────┐
│ Output folder [ ~/reports/_analyst   ]  [ Browse ]      │
│                                                         │
│ Source                                                  │
│  (•) A folder     ( ) From a saved scan                 │
│  Saved scan  [ (none)            ▾ ]  [ Reload ]         │
│                                                         │
│ Model server                                            │
│  (•) Local        ( ) Remote AI box   [split card]      │
│  Host  [ 10.0.0.5      ]   Port [ 11434 ]     [ Test ]   │
│  [x] Use TLS (https)                                    │
│  Auth  [ Token ▾ ]  [ ••••••••••••• ]                    │
│  ! Remote sends file text off this machine.             │
│                                                         │
│ Model                                                   │
│  [ qwen3.6:27b                     ▾ ]   [ Refresh ]     │
│  Found 5 models on this server.                         │
│  Digest recorded per run (auto).                        │
│                                                         │
│ [x] Offer a quick review after an extraction            │
│                                                         │
│               [ Cancel ]     [ Save ]                   │
└─────────────────────────────────────────────────────────┘
```

- **Model dropdown**: list the models the server reports. No typing long names.
  - Ollama: `GET /api/tags` lists all installed models with digest.
  - llama.cpp: `GET /v1/models` (OpenAI-compatible); usually one model unless a swap proxy
    is in front.
  - Refresh re-queries the current server. Empty list means the server is unreachable.
  - All models are treated the same. No tested/untested marking in this rewrite.
- **Digest recorded per run**: `analyst_runs.model_tag` and `model_digest` already exist.
  So every report says what produced it, even without a global pin.
- **Model server (Local / Remote)**: the remote path is a **split card** (section 11).
  Build the Local path in the core work. The remote controls can appear disabled or hidden
  until the remote card lands.

---

## 7. UI — Report view (two layers)

```
┌ Report — host12 ─────────────────────────────────────────────┐
│ WHAT THIS IS                                   Risk: ● HIGH   │
│ Small-business accounting server. Appears to belong to        │
│ Anytown Tax & Books LLC. Holds client tax returns and         │
│ payroll with SSNs and bank details.                           │
│                                                               │
│ Likely owner : Anytown Tax & Books LLC                        │
│ Contacts     : office@anytowntax.example, (555) 123-4567      │
│ Files read   : 310      Flagged files: 22                     │
│                                                               │
│ TOP EXPOSURES                                                 │
│  1. HIGH  Client SSNs in 2023_returns.xlsx (48 rows)          │
│  2. HIGH  Payroll bank accounts in payroll_q3.csv             │
│  3. MED   Owner personal cell in contacts.vcf                 │
│                                                               │
│ Model's read. Not verified. Facts below are grounded.         │
│──────────────────────────────────────────────────────────── │
│ FACTS   [All] [PII] [Financial] [Contact]          [ Export ] │
│ ┌────────┬────────────────┬───────────────────┬────────────┐ │
│ │ Kind   │ Value (quote)  │ File              │ Rank       │ │
│ ├────────┼────────────────┼───────────────────┼────────────┤ │
│ │ SSN    │ 123-45-6789    │ 2023_returns.xlsx │ HIGH       │ │
│ │ Phone  │ (555) 890-1212 │ mower_manual.pdf  │ low        │ │
│ └────────┴────────────────┴───────────────────┴────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

The lawnmower phone drops to "low" in Facts and never reaches Top Exposures.

---

## 8. UI — Batch export

Multi-select the runs, then one Export dialog handles all of them.

```
┌ Export reports ──────────────────────────────┐
│ Selected: 12 reports                         │
│                                              │
│ Format   [x] Markdown   [x] JSON             │
│          [ ] Plain text [ ] CSV (facts)      │
│                                              │
│ Layout   (•) One file per report             │
│          ( ) One combined file               │
│                                              │
│ Include  [x] The read    [x] The facts       │
│                                              │
│ Folder   [ ~/exports/analyst ]   [ Browse ]  │
│                                              │
│              [ Cancel ]    [ Export ]        │
└──────────────────────────────────────────────┘
```

Result line: "Exported 12 reports to ~/exports/analyst."

---

## 9. What we keep (do not discard)

The infrastructure is not the over-engineering. Keep it:

- The detached worker subprocess and the GUI to CLI boundary.
- Durable, resumable run state and the worker lease/fence.
- The bubblewrap parser sandbox. Files come from strangers' open directories; parsing is
  remote-code-execution territory (MuPDF risk, CONTRACT §10).
- Verbatim-quote grounding for the sensitive values in the facts layer.

The over-engineering to replace is the analytical contract: the four-tag chunk classifier,
the detector-first gating, and the coverage-first report.

---

## 10. Security relaxations approved (call-outs)

The operator's standing rule: build over-secure first, then relax deliberately for a
thought-out reason. These three relaxations are approved.

| # | Relax | From | To | Status |
|---|-------|------|----|--------|
| 1 | Report seal | fail closed, hide if changed | warn, do not block; still open | Approved |
| 2 | Model pin | fixed model + digest | user picks model; record model + digest per run | Approved |
| 3 | Model server | localhost only | optional remote with TLS + token, default off | Approved in principle; **split card** |

The PA must keep all other fail-closed behavior intact: sandbox, lease, two-attempt
semantics, no private content in logs/reprs/exceptions/tests/prompts.

---

## 11. Split into its own card: remote model server

The remote AI box path adds network and auth risk. It gets its own plan and its own review
gate, after the core report work.

Scope of the split card:

- Local stays the default and ships first.
- Remote requires TLS and a token. Default off.
- A "Test" button verifies the server, lists models, and records the chosen model + digest
  before any run.
- Hard rule from the Phase 1 handoff: raw port 11434 must never become the LAN/Tailscale
  interface. Remote access goes through an authenticated TLS path with an explicit access
  policy and no public-web exposure.
- Flag: extracted file text (often sensitive) leaves the machine on a remote run. The UI
  must say so.

---

## 12. Known constraints from prior work

Do not rediscover these:

- Models cannot produce reliable character offsets. The aggregator locates quotes itself
  by substring containment. (C0B-1)
- Prompt injection: 0 compliance events across all benchmark cells. Keep the untrusted
  nonce-fence discipline in every prompt.
- `gpt-oss` needs `/api/chat` (empty on `/api/generate` + `format`) and `num_predict >=
  2048` (the reasoning trace eats the budget).
- `qwen3.6:35b` (MoE) is 4 to 8x faster than the dense 27b.
- Local hardware: RTX 4060 Ti 16GB, 24 cores, 121GB RAM.
- Real corpus at `~/Documents/Extracted`: ~116k files, RTF ~71%. Full read of a large host
  is slow. Quick look matters.

---

## 13. Open questions for the PA

1. Define "Quick look" vs "Full read" in a read-first world. Proposed default: Quick reads
   a representative sample of files for a fast verdict; Full reads every supported file.
2. How does the model form a host-level read without exceeding context on a large host?
   Options: map-reduce over file summaries, a sampled skim, or a two-pass summarize-then-
   rank. The PA picks and justifies one.
3. What is the risk-rating rubric (HIGH / MED / LOW)? Keep it simple and documented.
4. How much of the existing worksheet/schema survives as the facts-layer extractor vs a
   new prompt? Prefer reuse where the grounding rigor already works.
5. Owner attribution is a model guess and can be wrong. Confirm the label wording that
   keeps it clearly unverified.
6. Report schema versioning: `report.json` needs a version field and a migration story,
   since this replaces the current artifact set.
