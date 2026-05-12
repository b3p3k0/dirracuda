# Risk Register: Pry/RCE Sunset

Scale:

- Likelihood: Low | Medium | High
- Impact: Low | Medium | High

## Active Risks

1. ID: R-01
- Risk: Partial runtime references remain after UI/CLI removal.
- Likelihood: Medium
- Impact: High
- Mitigation: enforce grep guardrails each card; add absence assertions in tests.
- Owner: Execution agent
- Status: Open

2. ID: R-02
- Risk: Hidden coupling in server list action routing causes regression in non-Pry actions.
- Likelihood: Medium
- Impact: High
- Mitigation: targeted tests for action routing and scenario matrix after each runtime card.
- Owner: Execution agent
- Status: Open

3. ID: R-03
- Risk: Legacy DB schema assumptions cause runtime errors when old tables/columns exist or vary.
- Likelihood: Medium
- Impact: High
- Mitigation: runtime schema checks before optional access; preserve non-destructive compatibility.
- Owner: Execution agent
- Status: Open

4. ID: R-04
- Risk: Oversized files become riskier during code removal edits.
- Likelihood: Medium
- Impact: Medium
- Mitigation: mandatory before/after line-count logging; stop-and-plan at >1700 lines touched.
- Owner: Execution agent
- Status: Open

5. ID: R-05
- Risk: Docs drift from implemented behavior after sunset.
- Likelihood: Medium
- Impact: Medium
- Mitigation: C6 requires README + TECHNICAL_REFERENCE sync and final grep evidence.
- Owner: PA/RA
- Status: Open

6. ID: R-06
- Risk: Test target churn if Pry/RCE test files are removed or renamed mid-stream.
- Likelihood: Medium
- Impact: Medium
- Mitigation: allow replacement targets with explicit rationale and equivalent coverage statement.
- Owner: Execution agent
- Status: Open

7. ID: R-07
- Risk: `BatchStatusDialog` (`pry_status_dialog.py`) is shared between Pry, probe, extract, Reddit browser, and SE dork browser. Surgical removal of only the `"pry"` job type branch risks breaking other job type display if the boundary is misidentified.
- Likelihood: Medium
- Impact: High
- Mitigation: Confirm `"pry"` job type map entry is isolated in `batch_status.py:128` before editing; run probe/extract status display tests post-C2.
- Owner: Execution agent
- Status: Open

8. ID: R-08
- Risk: `reddit_browser_window.py` (1308 lines, Good) and `se_dork_browser_window.py` (1054 lines, Excellent) both import `BatchStatusDialog` from `pry_status_dialog.py`. Renaming or splitting that file during C2 breaks these imports silently.
- Likelihood: Low
- Impact: High
- Mitigation: Do not rename `pry_status_dialog.py`; edit internals only.
- Owner: Execution agent
- Status: Open

## Risk Review Cadence

1. Review at start and end of each card.
2. Promote risk to High priority if it blocks card progression twice.
3. Capture newly discovered risks in this file before advancing to next card.
