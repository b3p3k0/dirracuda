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
