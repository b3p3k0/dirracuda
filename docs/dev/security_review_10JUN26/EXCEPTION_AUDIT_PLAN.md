# Exception Audit Plan

## Purpose

This plan governs the current-wave review of every pass-only exception handler in
product code. The baseline is 448 handlers across 103 files. Tests are excluded.

The count is not itself a vulnerability. The risk is that intentional silence,
diagnostic-worthy failures, and operator-facing failures are currently
indistinguishable without a complete audit.

## Baseline Method

The baseline was produced with an AST walk over these product roots:

- `commands/`: 6 handlers
- `experimental/`: 33 handlers
- `gui/`: 375 handlers
- `shared/`: 29 handlers
- `tools/`: 5 handlers

A handler is in scope when its body has no executable behavior beyond `pass`,
optionally accompanied by a docstring. Bare handlers and typed handlers are both
included. Test files and generated/vendor content are excluded.

The baseline is frozen at `development` commit `4320614`. Card E0 must rerun the
inventory before remediation and explain any drift rather than silently changing
the denominator.

## Required Classification

Every handler receives exactly one disposition:

| Classification | Meaning | Required action |
|---|---|---|
| `intentional-silent` | Silence is deliberate and preserves expected control flow. | Retain the handler and record a concise rationale in the audit ledger. Add a code comment only when future readers otherwise cannot infer the intent. |
| `should-log-debug` | The failure is recoverable but useful for diagnosis. | Add privacy-safe debug logging using the repository's established logger and message style. |
| `should-surface` | The failure affects correctness, completion, or an operator decision. | Propagate, return structured failure, update UI state, or show an operator-safe error through the established boundary. |

Classification must be based on behavior and call context, not exception type
alone. In particular, `tk.TclError`, `queue.Empty`, `StopIteration`, teardown
failures, and best-effort cleanup may be intentionally silent.

## Remediation Rules

- Do not apply blanket logging.
- Do not log credentials, URLs containing secrets, downloaded content, remote
  filenames when unnecessary, auth headers, cookies, or exception representations
  that may embed such data.
- Preserve Tk thread ownership and route GUI notices through
  `gui.utils.safe_messagebox` or the component's established `_mb()` path.
- Preserve the GUI-to-CLI subprocess boundary.
- Avoid logging in hot polling loops unless rate-limited or naturally one-shot.
- Narrow exception types only when the behavior remains compatible and tests prove
  the intended failure mode.
- Each implementation card may cover at most 40 baseline handlers.
- A handler is complete only when its classification, rationale, code disposition,
  and validation evidence are recorded.

## Batch Assignments

The batches are risk ordered: network and persistence paths first, then browser
cores and operational dialogs, followed by lower-risk presentation and utility
surfaces. `X001` through `X448` are stable audit identifiers for this baseline.

| Batch | IDs | File | Handler lines and caught types |
|---|---|---|---|
| E01 | X001-X002 | `commands/discover/connection_pool.py` | 59 bare; 82 bare |
| E01 | X003-X006 | `commands/ftp/verifier.py` | 76, 91, 117, 142 `Exception` |
| E01 | X007-X008 | `gui/utils/backend_interface/config.py` | 210 `Exception`; 231 `ImportError` |
| E01 | X009-X011 | `gui/utils/backend_interface/interface.py` | 810 typed; 909, 915 `OSError` |
| E01 | X012-X017 | `gui/utils/backend_interface/process_runner.py` | 179 typed; 214, 224 `Exception`; 233 typed; 239, 245 `Exception` |
| E01 | X018-X019 | `gui/utils/backend_interface/progress.py` | 86 `Exception`; 223 `ValueError` |
| E01 | X020-X021 | `gui/utils/database_access.py` | 66, 72 `Exception` |
| E01 | X022 | `gui/utils/db_unification.py` | 237 `ValueError` |
| E01 | X023-X029 | `gui/utils/extract_runner.py` | 423, 433, 441, 485, 505, 637, 822 `Exception` |
| E01 | X030-X031 | `gui/utils/ftp_probe_cache.py` | 67, 79 `Exception` |
| E01 | X032 | `gui/utils/ftp_probe_runner.py` | 187 `Exception` |
| E01 | X033-X034 | `gui/utils/http_probe_cache.py` | 85, 101 `Exception` |
| E01 | X035-X036 | `gui/utils/probe_cache.py` | 70, 83 `Exception` |
| E01 | X037 | `gui/utils/probe_cache_dispatch.py` | 79 `Exception` |
| E01 | X038 | `gui/utils/probe_runner.py` | 251 `Exception` |
| E01 | X039-X040 | `gui/utils/protocol_extract_runner.py` | 313, 356 `Exception` |
| E02 | X041-X045 | `gui/utils/protocol_extract_runner.py` | 369, 489, 814 `Exception`; 507, 513 `OSError` |
| E02 | X046-X050 | `gui/utils/scan_manager.py` | 199, 266, 479, 897 `Exception`; 361 typed |
| E02 | X051-X052 | `gui/utils/sidecar_promotion.py` | 117, 190 `Exception` |
| E02 | X053 | `shared/config.py` | 213 `Exception` |
| E02 | X054 | `shared/config_store.py` | 165 `OSError` |
| E02 | X055 | `shared/database.py` | 45 `Exception` |
| E02 | X056 | `shared/db_migrations.py` | 716 `Exception` |
| E02 | X057-X061 | `shared/ftp_browser.py` | 125 `Exception`; 202 typed; 323 `ValueError`; 409 `OSError`; 454 `StopIteration` |
| E02 | X062-X067 | `shared/http_browser.py` | 273, 286, 304, 312, 319 `OSError`; 296 `Exception` |
| E02 | X068-X069 | `shared/path_service.py` | 320, 833 `Exception` |
| E02 | X070 | `shared/quarantine.py` | 105 `Exception` |
| E02 | X071-X075 | `shared/smb_adapter.py` | 330, 334, 389, 397, 470 `Exception` |
| E02 | X076-X080 | `shared/smb_browser.py` | 119, 237, 248, 255, 262 `Exception` |
| E03 | X081 | `shared/smb_browser.py` | 312 `StopIteration` |
| E03 | X082 | `experimental/censys_discovery/service.py` | 227 `Exception` |
| E03 | X083 | `experimental/redseek/explorer_bridge.py` | 55 `ValueError` |
| E03 | X084 | `experimental/redseek/service.py` | 314 `Exception` |
| E03 | X085 | `experimental/se_dork/main_db_sync.py` | 67 `Exception` |
| E03 | X086-X092 | `experimental/se_dork/service.py` | 171, 183, 262, 645, 997, 1102, 1196 `Exception` |
| E03 | X093 | `experimental/webui/config.py` | 456 `OSError` |
| E03 | X094-X095 | `experimental/webui/experimental_features.py` | 293, 357 `Exception` |
| E03 | X096 | `experimental/webui/rate_limiter.py` | 287 `sqlite3.Error` |
| E03 | X097-X098 | `experimental/webui/request_security.py` | 56, 80 `ValueError` |
| E03 | X099-X107 | `experimental/webui/service_control.py` | 153, 157, 165 `OSError`; 174, 191, 316 `ImportError`; 324 typed; 477 `Exception`; 522 `ProcessLookupError` |
| E03 | X108 | `experimental/webui/shared_jobs.py` | 228 `ValueError` |
| E03 | X109-X110 | `experimental/webui/systemd_control.py` | 253, 257 `OSError` |
| E03 | X111-X114 | `experimental/webui/tasks.py` | 452, 456, 467 `OSError`; 751 `TimeoutExpired` |
| E03 | X115 | `tools/add_share_uniqueness_constraint.py` | 207 bare |
| E03 | X116-X119 | `tools/failure_analyzer.py` | 198, 273, 278, 324 bare |
| E03 | X120 | `gui/browsers/core.py` | 313 `tk.TclError` |
| E04 | X121-X123 | `gui/browsers/core.py` | 448 `Exception`; 455, 463 `tk.TclError` |
| E04 | X124-X125 | `gui/browsers/factory.py` | 107, 117 `Exception` |
| E04 | X126-X145 | `gui/browsers/ftp_browser.py` | 62, 121, 536, 577, 617, 647, 658 `Exception`; 200, 205, 365, 438, 483, 543, 551, 558, 565, 598, 621, 645 `tk.TclError`; 223 typed |
| E04 | X146-X160 | `gui/browsers/http_browser.py` | 59, 141, 496, 601 `Exception`; 220, 225, 368, 427, 503, 511, 518, 525, 553, 571, 599 `tk.TclError` |
| E05 | X161 | `gui/browsers/http_browser.py` | 618 `Exception` |
| E05 | X162-X175 | `gui/browsers/smb_browser.py` | 77, 115, 184, 1038, 1070, 1081, 1085, 1285, 1291, 1328, 1412, 1425, 1434, 1462 `Exception` |
| E05 | X176-X193 | `gui/components/dashboard_batch_ops.py` | 84, 97, 285, 434, 441, 632, 736, 831, 897, 986, 1121, 1357, 1461 `Exception`; 148, 164, 201, 220, 265 `tk.TclError` |
| E05 | X194-X200 | `gui/components/dashboard_scan.py` | 99, 550, 713, 719, 743, 763 `Exception`; 512 `tk.TclError` |
| E06 | X201-X210 | `gui/components/dashboard_scan.py` | 803, 817, 845, 1305, 1312 `Exception`; 1181, 1256, 1286, 1318, 1414 `tk.TclError` |
| E06 | X211-X220 | `gui/components/dashboard_searxng_scan.py` | 63, 86, 175, 181, 219, 227, 248, 283, 298, 334 `Exception` |
| E06 | X221-X224 | `gui/components/server_list_window/actions/batch.py` | 217, 333, 400, 456 `Exception` |
| E06 | X225-X229 | `gui/components/server_list_window/actions/batch_operations.py` | 110, 538, 1148 `Exception`; 645, 697 `tk.TclError` |
| E06 | X230-X240 | `gui/components/server_list_window/actions/batch_status.py` | 112, 119, 284, 364, 385, 394, 401, 480, 521, 533, 578 `Exception` |
| E07 | X241 | `gui/components/server_list_window/actions/batch_status.py` | 709 `Exception` |
| E07 | X242-X248 | `gui/components/server_list_window/actions/templates.py` | 139, 164, 420, 424, 428, 434, 439 `Exception` |
| E07 | X249-X257 | `gui/components/server_list_window/details.py` | 191, 194, 237, 457, 630, 663, 681, 981, 1055 `Exception` |
| E07 | X258 | `gui/components/server_list_window/export.py` | 151 bare |
| E07 | X259-X261 | `gui/components/server_list_window/table.py` | 155 bare; 345, 368 `Exception` |
| E07 | X262-X269 | `gui/components/server_list_window/window.py` | 277, 292, 336, 361, 373, 385 `tk.TclError`; 625, 658 `Exception` |
| E07 | X270-X274 | `gui/components/app_config_dialog.py` | 510, 528, 604, 625 `tk.TclError`; 1201 `Exception` |
| E07 | X275-X277 | `gui/components/app_config_security_tab.py` | 17, 50, 62 `tk.TclError` |
| E07 | X278-X280 | `gui/components/batch_extract_dialog.py` | 119, 130, 145 `Exception` |
| E08 | X281-X284 | `gui/components/batch_extract_dialog.py` | 153, 400, 584, 1009 `Exception` |
| E08 | X285-X290 | `gui/components/ftp_scan_dialog.py` | 233, 244, 298, 1181, 1195, 1266 `Exception` |
| E08 | X291-X296 | `gui/components/http_scan_dialog.py` | 203, 214, 265, 951, 965, 1039 `Exception` |
| E08 | X297-X303 | `gui/components/scan_dialog.py` | 570, 764, 778, 868, 879, 914, 1008 `Exception` |
| E08 | X304-X305 | `gui/components/scan_preflight.py` | 172, 261 `Exception` |
| E08 | X306-X309 | `gui/components/scan_provider_options.py` | 300, 361, 380, 385 `tk.TclError` |
| E08 | X310-X316 | `gui/components/unified_scan_dialog.py` | 262, 274, 339, 719, 1025, 1130 `Exception`; 724 `tk.TclError` |
| E08 | X317-X320 | `gui/components/censys_browser_window.py` | 222, 230, 240 `Exception`; 526 `queue.Empty` |
| E09 | X321-X324 | `gui/components/dashboard_experimental.py` | 54, 202, 206, 241 `Exception` |
| E09 | X325-X326 | `gui/components/dashboard_logs.py` | 174 `queue.Empty`; 230 `tk.TclError` |
| E09 | X327-X329 | `gui/components/dashboard_provider_queue.py` | 334, 341 `Exception`; 374 typed |
| E09 | X330 | `gui/components/dashboard_scan_output_dialog.py` | 136 `tk.TclError` |
| E09 | X331 | `gui/components/data_import_dialog.py` | 575 bare |
| E09 | X332-X333 | `gui/components/database_setup_dialog.py` | 87 `Exception`; 578 `queue.Empty` |
| E09 | X334-X335 | `gui/components/db_tools_dialog.py` | 1243 `queue.Empty`; 1284 `tk.TclError` |
| E09 | X336-X340 | `gui/components/dorkbook_window.py` | 816, 833, 845, 852, 860 `Exception` |
| E09 | X341-X344 | `gui/components/experimental_features/censys_discovery_tab.py` | 199, 361, 372, 409 `Exception` |
| E09 | X345 | `gui/components/experimental_features/reddit_tab.py` | 125 `Exception` |
| E09 | X346-X351 | `gui/components/experimental_features/se_dork_tab.py` | 90, 159, 181, 200, 267, 283 `Exception` |
| E09 | X352-X358 | `gui/components/experimental_features/webui_tab.py` | 203, 220, 237, 506, 510, 936, 940 `Exception` |
| E09 | X359 | `gui/components/experimental_features_dialog.py` | 133 `Exception` |
| E09 | X360 | `gui/components/file_viewer_window.py` | 180 `Exception` |
| E10 | X361-X373 | `gui/components/help_manual_dialog.py` | 265, 273, 285, 296, 312, 333, 341, 350, 358, 366, 734, 759, 784 `Exception` |
| E10 | X374-X375 | `gui/components/image_viewer_window.py` | 151 `tk.TclError`; 207 `Exception` |
| E10 | X376-X384 | `gui/components/keymaster_window.py` | 1079, 1113, 1133, 1142, 1252, 1400, 1422, 1432, 1446 `Exception` |
| E10 | X385-X387 | `gui/components/pry_status_dialog.py` | 125, 133, 141 `Exception` |
| E10 | X388-X389 | `gui/components/query_budget_dialog.py` | 144, 206 `Exception` |
| E10 | X390-X397 | `gui/components/reddit_browser_window.py` | 546, 558, 594, 604, 850 `Exception`; 574 `tk.TclError`; 972 `ValueError`; 1174 `queue.Empty` |
| E10 | X398 | `gui/components/reddit_grab_dialog.py` | 183 `tk.TclError` |
| E10 | X399 | `gui/components/running_tasks_window.py` | 170 `Exception` |
| E10 | X400 | `gui/components/scan_dork_editor_dialog.py` | 169 `tk.TclError` |
| E11 | X401-X405 | `gui/components/scan_dork_editor_dialog.py` | 309, 392, 398, 413, 464 `Exception` |
| E11 | X406-X413 | `gui/components/se_dork_browser_window.py` | 269, 278, 288, 396, 408, 629 `Exception`; 760 `ValueError`; 965 `queue.Empty` |
| E11 | X414-X424 | `gui/dashboard/widget.py` | 438, 445 `tk.TclError`; 819, 824, 830, 835, 843, 851, 880, 975, 1624 `Exception` |
| E11 | X425 | `gui/demo.py` | 34 bare |
| E11 | X426 | `gui/utils/dialog_helpers.py` | 71 `tk.TclError` |
| E11 | X427 | `gui/utils/error_codes.py` | 67 typed |
| E11 | X428-X431 | `gui/utils/keybindings.py` | 23, 31, 232, 241 `Exception` |
| E11 | X432 | `gui/utils/probe_patterns.py` | 41 `Exception` |
| E11 | X433-X440 | `gui/utils/safe_messagebox.py` | 55, 74, 81, 91, 100, 104, 108, 118 `Exception` |
| E12 | X441-X442 | `gui/utils/safe_messagebox.py` | 122, 126 `Exception` |
| E12 | X443 | `gui/utils/settings_manager.py` | 663 `Exception` |
| E12 | X444-X445 | `gui/utils/style.py` | 542 `Exception`; 634 `tk.TclError` |
| E12 | X446-X448 | `gui/utils/ui_dispatcher.py` | 99, 128 `tk.TclError`; 102 `Exception` |

## Card Procedure

For each E01-E12 card, Claude must:

1. Reproduce the assigned handler list from the E0 ledger and report any line drift.
2. Inspect each handler's call path, state effects, thread context, and data sensitivity.
3. Submit a plan-only classification and proposed remediation for every assigned ID.
4. Wait for Codex and HI approval.
5. In a separate DA session, implement only the approved non-intentional changes.
6. Update the ledger with final classification, rationale, files changed, tests, and
   residual risk.

## Completion Rules

The exception audit is complete only when:

- IDs X001-X448 each appear exactly once in the ledger.
- Every ID has one approved classification and rationale.
- Every `should-log-debug` and `should-surface` item is remediated and validated.
- Every retained `intentional-silent` item has an auditable rationale.
- Batch sizes remain at or below 40 handlers.
- The post-remediation AST inventory is attached as evidence and any remaining
  pass-only handlers map to approved `intentional-silent` ledger entries.
- The full regression gates in `VALIDATION_PLAN.md` pass.

Line numbers are baseline locators, not permanent identity. The stable identity is
the X-ID plus file and surrounding handler context recorded by E0.
