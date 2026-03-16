# RCE Module Rebuild – Implementation Plan (Lead Dev Handoff)

Status: plan only (no code changes yet)  
Scope: smbseek optional RCE detection module (CLI + GUI)  
Audience: future agents/engineers and reviewers (Claude as implementer)  
Source inputs read: `docs/dev/RCE_Module/RCE_module.md`, `docs/dev/RCE_Module/RCE_SIGNATURE_WISHLIST.md`, current RCE code (`shared/rce_scanner`, `signatures/rce_smb`, GUI/CLI touchpoints)

---

## Confirmed decisions from HI
- Safe-active MS17-010 FID0 probe is allowed when RCE is enabled, but SMB1 probing must stay gated by `--legacy` (or equivalent GUI toggle).
- Safe-active SMB 3.1.1 negotiate + compression parse is allowed; defaults should be conservative with user-configurable limits in `conf/config.json`.
- Any available Shodan / existing library data can feed the fact model.
- Replace “score/level only” with verdict taxonomy: `CONFIRMED | LIKELY | NOT_VULNERABLE | NOT_ASSESSABLE | INSUFFICIENT_DATA | ERROR`.
- Logging may be full/transparent (no redaction); JSONL is fine.
- Lab targets will vary; design must be robust, not test-tailored.
- Provide an explicit (disabled-by-default) Intrusive/Unsafe mode stub for future MS08-067/MS10-054-style checks.
- GUI: add a server-list column with status icons (⭘ not run, ✓ clean, ✖ indicator found) and show a brief RCE block in the host details pane after shares/ransomware indicators.
- Passive re-analysis on cached data is not required.
- No global probe-concurrency cap required now (note for future).

---

## Current-state assessment (problems to fix)
- Fact model is too shallow: no dialect, signing, compression, status codes, Shodan product/version, domain role, or ms17_010_status are captured.
- Rule engine signals don’t align with collected facts; boosters are ignored; all findings are “low confidence” score-based only.
- No safe-active probe runner; no SMB negotiate parsing; no SMB1 gating tied to `--legacy`.
- Signatures are heuristic-only and can’t express verdicts/evidence cleanly.
- UI only displays score/level text; no column/status or “why not assessable.”
- No dedicated JSONL logging for RCE findings.

---

## Target outcomes (definition of done)
1) **Fact collection**: per-host facts include `smb_dialect`, `signing_required`, `compression_algos`, `smb1_possible`, `ms17_010_status`, Shodan product/version/OS hints, domain-role hints, ksmbd/Samba banners.  
2) **Safe-active probes** (opt-in):  
   - MS17-010 safe FID0 status probe (only when legacy/SMB1 allowed).  
   - SMB 3.1.1 negotiate + compression context parse.  
   - Probe budget enforced (see defaults below).  
3) **Rules/plugins**: verdict-based plugins for MS17-010 and SMBGhost; passive-only rules for Samba 7494/44142, ksmbd 2022-47939, legacy MS08-067/MS10-054 (NOT_ASSESSABLE/LIKELY only). Boosters evaluated.  
4) **Reporting/logging**: verdict + confidence + evidence strings; JSONL log per host; “why not assessable” reason always present.  
5) **UI/CLI**: new server-list RCE status column; detail view section showing verdict summary; CLI output mirrors new verdicts.  
6) **Config**: safe defaults with user overrides in `conf/config.json`.  
7) **Intrusive mode stub**: explicitly disabled flag and guardrails; no intrusive packets sent in default build.  
8) **Tests**: unit tests for fact collector, rule engine, probe parsers; replay harness with recorded negotiate/FID0 transcripts to avoid live exploits.

---

## Work plan (phased, sequential handoff)

### Phase 1 – Fact model & plumbing
- Extend `FactCollector` to ingest: Shodan product/version/OS/banners, domain-role hints (NETLOGON/SYSVOL), ksmbd/Samba markers, and new SMB fields (`smb_dialect`, `signing_required`, `compression_algos`, `ms17_010_status` placeholder).
- Normalize names to match RuleEngine signals (rename/add signal extractors instead of string contains).
- Decide DB persistence changes (see Data model section) and update readers/writers.

### Phase 2 – Safe probe runner
- Implement lightweight negotiate collector (SMB2/3; SMB1 only when legacy flag true).  
- Add MS17-010 FID0 status probe; map status codes to verdicts per spec.  
- Parse SMB 3.1.1 compression contexts into algorithm IDs.  
- Enforce probe budget + timeouts + jitter; integrate with existing timeouts/rate limits.  
- Config-driven switches (see defaults).

### Phase 3 – Plugin/rule overhaul
- Create plugin layer (even if thin wrappers) producing verdict + evidence.  
- Update signatures or migrate to Python plugins for: MS17-010, SMBGhost, Samba 7494, Samba 44142, ksmbd 2022-47939, legacy MS08-067/MS10-054 (passive).  
- Implement booster evaluation and required_signals enforcement; drop pure additive score as primary signal (keep optional).

### Phase 4 – Reporting & logging
- Reporter outputs verdict taxonomy and “not assessable because …”.  
- Add JSONL writer (path/configurable) with full evidence.  
- Keep CLI summary concise; include primary CVE + verdict.

### Phase 5 – UI/CLI integration
- Server list: new RCE status column with icons: ⭘ (not run), ✓ (no indicators / all NOT_VULNERABLE), ✖ (any CONFIRMED/LIKELY).  
- Detail view: short RCE block after shares/ransomware, listing top findings and reasons when not assessable.  
- Wire `--check-rce` + GUI toggles to safe-active/legacy gating.

### Phase 6 – Intrusive mode stub
- Config + CLI/GUI flag present but OFF; guards prevent packet send.  
- Log a warning when requested without enablement.

### Phase 7 – Tests & replay harness
- Unit tests for: fact extraction, signal mapping, MS17-010 status classification, SMB compression parsing, rule evaluation.  
- Add replay fixtures (pcap/hex blobs) for negotiate and FID0 responses to avoid live exploits.  
- Smoke tests: ensure no SMB1 probes unless legacy flag set; ensure probe budget enforced.

### Phase 8 – Docs & operator guidance
- Update USER/DEV guides and RCE docs: config keys, safe-active behavior, UI meaning, intrusive stub behavior, remediation mapping.

---

## Data model & config notes
- **DB/UI**: add `rce_status` per host (enum: not_run / clean / flagged) to back the server-list column; store last verdict summary for detail view. Decide on migration vs. transient in-memory cache (prefer DB column for persistence).  
- **Config additions (proposed defaults):**  
  - `rce.enabled_default`: false  
  - `rce.safe_active_budget.max_requests`: 2 (1 negotiate + 1 optional MS17-010)  
  - `rce.safe_active_budget.per_host_timeout_seconds`: 5  
  - `rce.safe_active_budget.retry_count`: 0  
  - `rce.safe_active_budget.jitter_ms`: 250  
  - `rce.ms17_010.enabled`: true (only honored if legacy/SMB1 allowed)  
  - `rce.smbghost.enabled`: true  
  - `rce.logging.jsonl_path`: `~/.smbseek/logs/rce_analysis.jsonl`  
  - `rce.intrusive_mode_enabled`: false (guard)  
  - `rce.ui.show_column`: true  
  These should be added to `conf/config.json.example` with comments.

---

## Risk & probing posture (SWOT for more aggressive stance)
- **Strengths:** Safe-active MS17-010 confirmation; SMBGhost exposure clarity; better operator guidance.  
- **Weaknesses:** More wire noise; SMB1 negotiate can trip IDS or rare unstable legacy stacks.  
- **Opportunities:** Optional fingerprinting mode (negotiate conformance), optional pipe-open checks for PetitPotam/ZeroLogon exposure (still passive).  
- **Threats:** Misconfiguration sending SMB1 broadly; future intrusive toggle misuse; false reassurance if gating/NOT_ASSESSABLE messaging is unclear.

---

## Implementation map to files (for Claude)
- Fact plumbing: `shared/rce_scanner/fact_collector.py`, `shared/rce_scanner/rules.py` signal extractors, `shared/rce_scanner/scanner.py` wiring, `shared/rce_scanner/__init__.py`.
- Probes: new helper (e.g., `shared/rce_scanner/probes.py`), and touch `commands/access/operation.py` & `gui/utils/probe_runner.py` for enabling safe-active modes.
- Plugins/signatures: either evolve YAML under `signatures/rce_smb/` or create Python plugins under `shared/rce_scanner/plugins/`; ensure boosters implemented.
- Reporting/logging: `shared/rce_scanner/reporter.py` and new `shared/rce_scanner/logger.py` (JSONL).  
- UI: `gui/components/server_list_window` (column + details), `gui/components/scan_preflight.py`, `gui/components/scan_dialog.py`, `gui/utils/probe_runner.py`, `gui/utils/probe_cache.py`.  
- CLI: `smbseek` argparse help; `shared/workflow.py` to pass flags; `commands/access/operation.py` to capture/store rce_status.  
- Config: `conf/config.json.example`, config loader, and any settings manager touchpoints.
- DB: `shared/database.py` migration for `rce_status` column; update `probe_cache` persistence if applicable.

---

## Open items to resolve during build
- Exact icon characters and color mapping in Tk (ensure accessibility in light/dark themes).  
- Decide whether to store full findings JSON in DB or just summary + status.  
- Backfill existing scan records with `rce_status = not_run` during migration.

---

## Hand-off checklist
- Use this plan as the authoritative scope.  
- No intrusive packets in default flow; legacy/SMB1 required for MS17-010.  
- Keep evidence strings concise and audit-friendly.  
- Prioritize stability of negotiate parser and status-code classifier.  
- After implementation, update this folder’s docs to reflect final behavior.
