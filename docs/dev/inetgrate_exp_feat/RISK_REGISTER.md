# Integrate Experimental Features Into Main - RISK REGISTER

Status: Active
Last updated: 2026-05-29

## Open Risks

| ID | Risk | Likelihood | Impact | Mitigation | Owner | Card |
| --- | --- | --- | --- | --- | --- | --- |
| R1 | Start-scan refactor breaks existing SMB/FTP/HTTP launch behavior | Medium | High | Keep protocol path intact while adding provider scaffolding; targeted regression after each card | DA + RA | C2-C4 |
| R2 | Provider promotion introduces UI-thread blocking | Medium | Medium | Keep provider runs in worker paths; avoid long synchronous calls in Tk handlers | DA | C3-C4 |
| R3 | Sidecar import writes fail on schema drift | High | High | Runtime table/column checks before reads/writes; per-row failure reporting | DA | C5 |
| R4 | Config migration drops provider values | Medium | High | Backward-compatible read paths, versioned migration, explicit fallback logic | DA + RA | C6 |
| R5 | Terminology cutover (Experimental -> Accessories) confuses tests/docs | Medium | Medium | Update tests/docs in same card; keep behavior aliases where needed | DA | C1 |
| R6 | Accidental Censys resurfacing in UI/contracts | Low | Medium | Explicit out-of-scope checks in every card prompt/review | RA | All |
| R7 | Large touched production-code files exceed maintainable size | Medium | Medium | Enforce 1700-line stop rule and modularization proposal gate (tests/docs excluded from hard stop) | DA + RA | All |
| R8 | External API behavior/policy changes invalidate assumptions | Medium | Medium | Re-check vendor docs at planning points; cite current sources in contracts | RA | All |
| R9 | Regression gaps due incomplete test targeting | Medium | High | Card-level targeted tests + quick-lane before closeout | DA + RA | C7 |
| R10 | One-time migration prompt logic repeats too often or not at all | Medium | Medium | Persist explicit migration state machine and test first-run/defer/resume branches | DA + RA | C5 |

## Closed Risks

| ID | Risk | Resolution |
| --- | --- | --- |
| R11 | WebUI continues exposing sidecar DB surfaces after promotion | Mitigated in C6: SearXNG/Reddit sidecar result-browse/probe/promote API routes removed from `app.py`. Dorkbook and Keymaster WebUI management APIs remain intentionally registered (`app.py:1087`, `keymaster_routes.py:69`). Desktop-owned migration messaging in place per lesson 12. |

## Monitoring Notes

- Re-check Shodan credit semantics before finalizing cost copy in UI.
- Re-check Reddit terms/rate-limit policy before changing ingestion defaults.
- Keep Censys notes documentation-only while module remains suspended.
- Treat migration execution as desktop-owned and WebUI notice as informational-only.
