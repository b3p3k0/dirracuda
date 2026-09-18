# Analyst Read-First UI Contract

- Date: 2026-09-18
- Status: drafted for freeze (R0). Layouts are normative; exact strings are frozen where noted.
- Conventions (from AGENTS.md / frozen §15): `safe_messagebox`, `ensure_dialog_focus` on every
  `grab_set` Toplevel, `SMBSeekTheme.apply_to_widget` named styles only, no worker-thread widget
  teardown. Both analyst GUI files miss `ensure_dialog_focus` today; add it when touched.

---

## 1. Screen 1 — launch and runs

One real input: the folder. Everything else is optional or under Advanced.

```
+-- Analyst -----------------------------------------------------------+
| Point Analyst at a folder of a host's extracted files.              |
| It reads them and tells you what the host is and                    |
| what is worth your attention.                                       |
|                                                                     |
| Folder  [ /home/kevin/Extracted/host12        ]  [ Browse ]         |
| Name    [ host12                    ]   optional                    |
|                                                                     |
| Read    (o) Quick look     ( ) Full read                            |
|                                                                     |
| [ Analyze ]                                          [ Advanced... ] |
|-------------------------------------------------------------------- |
| Runs                                              [ Select all ]     |
| +--+------------+--------+-----------+---------------------------+   |
| |[x]| host12     | Quick  | Running   | 40% read                 |   |
| |[x]| test_1     | Quick  | Done      | ● HIGH risk              |   |
| +--+------------+--------+-----------+---------------------------+   |
| [ Open report ]  [ Export selected... ]  [ Resume ]  [ Cancel ]     |
| Status: Reading host12...                                           |
+---------------------------------------------------------------------+
```

Changes from today:

- One required input: the folder. Name auto-fills from the folder basename.
- Output path, saved-scan source, model server, and engine info move to Advanced.
- Depth label "Fast / Deep" becomes **"Read: Quick look / Full read."**
- Runs column "Coverage" becomes **"Result"**: `● HIGH/MED/LOW risk` for a finished run (from
  `analyst_read.risk_level`), or `N% read` for an active run.
- Runs support multi-select for batch export.

The tab makes no Ollama contact on open (lesson 161). Model discovery is in Advanced and is
charged (§2).

---

## 2. Screen 2 — Advanced (its own dialog)

A separate dialog, not a dropdown, so it has room to grow. `grab_set` + `ensure_dialog_focus`.

```
+-- Analyst - Advanced ---------------------------------------+
| Output folder [ ~/reports/_analyst   ]  [ Browse ]         |
|                                                            |
| Source                                                     |
|  (o) A folder     ( ) From a saved scan                    |
|  Saved scan  [ (none)            v ]  [ Reload ]           |
|                                                            |
| Model server                                               |
|  (o) Local        ( ) Remote AI box   [later card]         |
|  Host  [ 127.0.0.1     ]   Port [ 11434 ]     [ Test ]     |
|                                                            |
| Model                                                      |
|  [ qwen3.6:27b                     v ]   [ Refresh ]       |
|  Found 5 models on this server.                            |
|  Digest recorded per run (auto).                           |
|                                                            |
| [x] Offer a quick review after an extraction               |
|                                                            |
|               [ Cancel ]     [ Save ]                      |
+------------------------------------------------------------+
```

- **Model dropdown** lists what the server reports via `/api/tags`, non-cloud only. No typing
  long names. `Refresh` re-queries. An empty list means the server is unreachable.
- **Refresh / Test are charged control contacts.** They are explicit and user-initiated, and
  each is recorded in `analyst_ollama_contacts` like any control contact. Never an on-open probe.
- **Digest per run**: the chosen tag and its resolved digest are recorded per run
  (`analyst_runs.model_tag` / `model_digest`), so every report says what produced it.
- **Model server Local / Remote**: build Local now. Remote controls appear disabled with a
  "later card" note until the separately reviewed remote card lands. Remote will require TLS and
  a token, default off, and will warn that file text leaves the machine.

---

## 3. Screen 3 — report view (two layers)

```
+-- Report - host12 ---------------------------------------------------+
| WHAT THIS IS                                       Risk: ● HIGH      |
| Small-business accounting server. Appears to belong to               |
| Anytown Tax & Books LLC. Holds client tax returns and                |
| payroll with SSNs and bank details.                                  |
|                                                                      |
| Likely owner : Anytown Tax & Books LLC                               |
| Contacts     : office@anytowntax.example, (555) 123-4567             |
| Files read   : 310      Flagged files: 22                            |
|                                                                      |
| TOP EXPOSURES                                                        |
|  1. HIGH  Client SSNs in 2023_returns.xlsx (48 rows)                 |
|  2. HIGH  Payroll bank accounts in payroll_q3.csv                    |
|  3. MED   Owner personal cell in contacts.vcf                        |
|                                                                      |
| Model's read - not verified. Facts below are grounded.               |
|-------------------------------------------------------------------- |
| FACTS   [All] [PII] [Financial] [Contact]      [ Export ] [ Copy ]   |
| +--------+----------------+-------------------+------------+         |
| | Kind   | Value (quote)  | File              | Rank       |         |
| +--------+----------------+-------------------+------------+         |
| | SSN    | 123-45-6789    | 2023_returns.xlsx | HIGH       |         |
| | Phone  | (555) 890-1212 | mower_manual.pdf  | low        |         |
| +--------+----------------+-------------------+------------+         |
+---------------------------------------------------------------------+
```

Frozen strings and rules:

- The read block always shows the exact line **"Model's read - not verified. Facts below are
  grounded."**
- `Likely owner` and `Contacts` are always prefixed to read as unverified.
- The read leads; the facts table backs it. A `low` fact never appears in Top Exposures.
- Values render as text nodes only (no dynamic HTML), same rule Sherlock follows.
- If the report folder changed since save, show a **"changed since saved"** badge and still open
  it (warn-not-block). Never present a changed report as verified.

---

## 4. Screen 4 — batch export

Multi-select runs on Screen 1, then one Export dialog handles all of them.

```
+-- Export reports --------------------------------+
| Selected: 12 reports                            |
|                                                 |
| Format   [x] Markdown   [x] JSON                |
|          [ ] Plain text [ ] CSV (facts)         |
|                                                 |
| Layout   (o) One file per report                |
|          ( ) One combined file                  |
|                                                 |
| Include  [x] The read    [x] The facts          |
|                                                 |
| Folder   [ ~/exports/analyst ]   [ Browse ]     |
|                                                 |
|              [ Cancel ]    [ Export ]           |
+-------------------------------------------------+
```

Result line: `Exported 12 reports to ~/exports/analyst.`

Export and Copy render from `report.json`. Markdown is primary; text, JSON, HTML, and facts CSV
render on demand. CSV cells keep the frozen formula-injection guard.

---

## 5. What the report view drops from today

- Coverage no longer leads. It becomes a "Files read / Flagged files" line plus a details view.
- The four-tag accept/reject picker (U4) is retired. The read plus ranked facts replace it.
- The report view now offers Copy (whole report to clipboard) and batch export, which today's
  view lacks.
