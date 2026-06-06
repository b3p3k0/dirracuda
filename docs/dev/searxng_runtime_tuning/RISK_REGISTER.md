# C11 Risk Register

Last updated: 2026-06-06

| ID | Risk | Guardrail |
| --- | --- | --- |
| R1 | Cancellation deletes useful current-page rows. | Delete only rows still unclassified; preserve rows after classification commit. |
| R2 | A cancelled queue advances from a stale callback. | Invalidate generation before signalling the active event; cancelled completion never calls normal advancement. |
| R3 | Thread-pool shutdown waits for all probes. | Signal probes, stop new submissions, cancel pending futures, and persist completed outcomes. |
| R4 | Retry sliders imply control over productive soft pacing. | Label controls as request timeout and hard retries; keep 10/20/30 fixed. |
| R5 | `Retry-After` bypasses mature-run retry count. | It replaces a delay slot but does not add another retry. |
| R6 | ANSI escapes leak into service/WebUI data. | Apply semantic colors only in dashboard presentation helpers. |
| R7 | Start Scan becomes vertically crowded. | Use three compact rows in the SearXNG satellite and verify default/minimum geometry. |
| R8 | Live script touches operator data. | Temporary DB by default; no primary sync; explicit `--keep-db`. |
| R9 | Live script becomes an accidental CI dependency. | Require `--confirm-live`; do not import it from pytest or agent workflow lanes. |
| R10 | Near-limit production files exceed policy. | Extract before editing; check line counts before and after every card. |
| R11 | Settings/templates restore invalid values. | Clamp at load, request construction, and service boundaries. |
| R12 | Cancellation is reported as failure. | Add explicit cancelled status and dedicated desktop completion branch. |

