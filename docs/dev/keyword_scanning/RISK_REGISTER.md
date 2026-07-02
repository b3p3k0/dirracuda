# Sherlock Risk Register

| ID | Risk | Severity | Mitigation | Validation |
| --- | --- | --- | --- | --- |
| R1 | Feature accidentally downloads or reads content | High | Snapshot-path-only matcher, no network/file APIs in matcher | Unit tests with sentinel objects and code review |
| R2 | Sherlock changes compromised/probe status semantics | High | Store/display in separate Sherlock state | Regression tests around probe status and indicator matches |
| R3 | DB migration breaks legacy DBs | High | Additive tables only, runtime table/column guards | Migration tests on minimal and current schemas |
| R4 | Pattern UI grows past dialog bounds | Medium | Fixed-height Treeview with scrollbar/mouse wheel/arrows | Xvfb screenshot/layout tests |
| R5 | Color-only indication hurts usability | Medium | Risk text is mandatory for findings | UI tests assert text values |
| R6 | Blank Risk column is confused with failed scan | Medium | Details/summaries explain skipped/no snapshot; table stays quiet | Detail tests and HI manual check |
| R7 | False positives create analyst noise | Medium | Disable-able built-ins, editable customs, capped details | Pattern tests and seeded sample review |
| R8 | Web UI DB file grows past guardrail; `experimental/webui/db.py` starts at 1687 lines | Medium | Put annotation logic in new helper module and keep `db.py` near zero net growth | File-size check before/after |
| R9 | Config shard ownership missing for Sherlock | Medium | Register `sherlock` module in config-store before UI save | Config-store tests |
| R10 | Dialog focus/pop-under regression | Low | Use safe messagebox and focus helper conventions | Existing guardrail tests plus Xvfb check |
| R11 | Matcher becomes storage-coupled during C1 | Medium | Define a pure path-entry input shape and keep DB reads in C2/C4 | Matcher purity tests and code review |
| R12 | Standalone and post-probe Sherlock scans drift apart | Medium | C4 factors a reusable scan-and-persist helper for C5 | C4/C5 integration tests |
| R13 | User colors are mistaken for severity | Medium | Keep severity text HIGH/MED/LOW unchanged; document User1/User2/User3 as visual tags only | Matcher/display tests assert unchanged severity/count |
| R14 | Tint precedence hides urgency | Medium | User color may override row tint, but Risk text remains severity-based and visible | UI tests assert Risk text and row tag/color behavior |
| R15 | Pattern popup edits persist unexpectedly | Medium | Popup stages edits in memory; only main Sherlock Save writes settings | GUI tests around staged edit/cancel/save behavior |
| R16 | Additive tag columns break older DBs | High | Nullable columns plus runtime table/column guards | Migration/minimal-schema tests |
| R17 | Probe summary silently changes non-Sherlock rows | Medium | Add Risk column only when at least one fresh finding exists; blank rows remain quiet | Batch summary tests and CSV tests |
| R18 | Near-limit files grow during display updates | Medium | Put shared tint/risk resolution in small helpers and check line counts before/after | File-size checks; modularization pause over 1700 lines |
| R19 | Built-in restore semantics become ambiguous | Medium | Built-ins are code-defined; edits create custom copies; deleted/disabled built-in state is separate and clearable by Restore Built-ins | Serialization tests and staged UI tests |
| R20 | Filtered/hidden rows are mutated by bulk actions | Medium | Filtering clears selection; bulk actions operate only on visible selected rows | Filter + bulk-action tests |
| R21 | Multi-select breaks single-row edit/copy expectations | Medium | Edit/Copy require exactly one selected row; batch actions support one or many rows | Selection/action tests |
| R22 | Export output is incomplete or not round-trip-friendly | Low | JSON exports the full staged list with metadata and all pattern fields; no import in this pass | Export schema and cancel/error tests |
| R23 | Pattern manager grows beyond maintainable size | Medium (retired) | Resolved by the C21 extraction. Pattern-manager logic now spans four files, all well under 1200: `sherlock_pattern_manager.py` 1019, `sherlock_tab.py` 584, `sherlock_value_actions.py` 352, `sherlock_category_actions.py` 340 (C26 measurement). `pm.py` at 1019 is the new watch item — split it before adding another major sub-feature | Line-count checks and closeout review |
| R24 | Helper extraction changes existing Pattern Manager behavior | Medium | C21 is behavior-preserving only; run existing C15-C19 tests and Xvfb parity before any new UI work | Existing Sherlock tab/export tests and screenshot diff review |
| R25 | Grouped comma rows accidentally change matcher or settings semantics | High | Keep comma rows as UI grouping only; persist one `SherlockPattern.pattern` string per token | Pure grouping tests, matcher regression, export regression |
| R26 | Literal comma in a desired pattern cannot be represented | Low | Document commas-inside-patterns as unsupported in this pass; split only on literal commas and test the limitation | Splitter tests and technical reference note |
| R27 | Category Delete removes too much data | Medium | Require confirmation with value/pattern counts; category actions mutate staged data only until Save | GUI delete confirmation/cancel tests |
| R28 | Bulk tag assignment changes severity or match text unexpectedly | Medium | Tag Apply updates only `color_tag` on selected value rows; severity, label, and pattern strings remain unchanged | Selected-row tag assignment tests |
| R29 | README screenshot or wording becomes stale/too technical | Low | C26 requires at least one current Pattern Manager screenshot and user-manual wording; technical details stay in TECHNICAL_REFERENCE | README review and screenshot path check |

## C26 Closeout Status

- **R23 retired.** The C21 extraction resolved the near-limit risk; file sizes
  recorded above. `sherlock_pattern_manager.py` (1019) is the new watch item.
- **R24 (helper extraction changes behavior) retired.** C21–C25 shipped behavior-
  preserving; the full Sherlock test matrix stays green.
- **R25, R27, R28 closed.** Grouped comma rows persist one `SherlockPattern` per
  token (matcher/settings/export unchanged); category Delete confirms with counts
  and mutates only staged data; Tag Apply changes only `color_tag` on selected
  value rows and skips built-ins. Covered by grouping/GUI tests.
- **R26 remains accepted (Low).** Literal commas inside a single pattern are
  unsupported; the splitter always treats commas as separators. Documented in
  README and TECHNICAL_REFERENCE §6.9; no fix planned this pass.
- **R29 closed.** README carries a current two-pane Pattern Manager screenshot
  (`img/sherlock_patterns.png`) and user-manual wording; technical depth lives in
  TECHNICAL_REFERENCE §6.9.
- **R1–R7, R13–R14 unchanged.** Security/display invariants are untouched by the
  two-pane redesign (still snapshot-path-only, severity text preserved).
