# Security Review Execution Tracker

Status: HI approved PA pack; Codex operating as RA; C5 accepted

## Baseline

| Field | Value |
|---|---|
| Date | 2026-06-10 |
| Branch | `development` |
| Commit | `4320614` |
| Worktree | Clean except untracked planning workspace |
| Python | 3.13.7 |
| Targeted tests | 95 passed |
| Pass-only handlers | 448 across 103 files |
| `xvfb-run` | `/usr/bin/xvfb-run` |

## Status Values

- `NOT STARTED`
- `PLANNING`
- `PLAN APPROVED`
- `IMPLEMENTING`
- `RA REVIEW`
- `MANUAL PENDING`
- `ACCEPTED`
- `BLOCKED`

## Card Ledger

| Card | Status | Approved plan | Automated | Manual | Commit | Notes |
|---|---|---|---|---|---|---|
| C0 | ACCEPTED | n/a | PASS | n/a | n/a | Baseline unchanged from PA pack |
| C1 | ACCEPTED | `approved_plans/C1_http_redirect_policy.md` | PASS | n/a | this commit | RA corrected IDNA wire encoding |
| C2 | ACCEPTED | `approved_plans/C2_endpoint_pinning.md` | PASS | n/a | this commit | Broad suite has one proven pre-existing failure |
| C3 | ACCEPTED | `approved_plans/C3_http_tls_policy.md` | PASS | PASS | this commit | RA corrected pre-modular explicit-value detection |
| C4 | ACCEPTED | `approved_plans/C4_smb_extract_containment.md` | PASS | n/a | this commit | SMB extraction containment |
| C5 | ACCEPTED | `approved_plans/C5_image_pixel_guard.md` | PASS | PASS | this commit | Pre-decode image pixel guard |
| C6 | NOT STARTED |  | PENDING | PENDING |  | Bounded ZIP import |
| C7 | NOT STARTED |  | PENDING | PENDING |  | FTP controls |
| C8 | NOT STARTED |  | PENDING | PENDING |  | SMB basename |
| E0 | NOT STARTED |  | PENDING | n/a |  | Exception ledger freeze |
| E01 | NOT STARTED |  | PENDING | PENDING |  | X001-X040 |
| E02 | NOT STARTED |  | PENDING | PENDING |  | X041-X080 |
| E03 | NOT STARTED |  | PENDING | PENDING |  | X081-X120 |
| E04 | NOT STARTED |  | PENDING | PENDING |  | X121-X160 |
| E05 | NOT STARTED |  | PENDING | PENDING |  | X161-X200 |
| E06 | NOT STARTED |  | PENDING | PENDING |  | X201-X240 |
| E07 | NOT STARTED |  | PENDING | PENDING |  | X241-X280 |
| E08 | NOT STARTED |  | PENDING | PENDING |  | X281-X320 |
| E09 | NOT STARTED |  | PENDING | PENDING |  | X321-X360 |
| E10 | NOT STARTED |  | PENDING | PENDING |  | X361-X400 |
| E11 | NOT STARTED |  | PENDING | PENDING |  | X401-X440 |
| E12 | NOT STARTED |  | PENDING | PENDING |  | X441-X448 |
| C9 | NOT STARTED |  | PENDING | PENDING |  | Final docs and regression |

## Evidence Template

Append one section per card:

```markdown
## <Card> Evidence

- Plan:
- DA session:
- Commit before:
- Commit after:
- Files changed:
- Line counts:
- Commands:
- Results:
- RA findings:
- Manual test:
- Residual risk:
- Final status:
```

## Exception Totals

Populate after E12:

| Classification | Count |
|---|---:|
| `intentional-silent` | PENDING |
| `should-log-debug` | PENDING |
| `should-surface` | PENDING |
| Total | 448 |

## C0 Evidence

- Plan: `TASK_CARDS.md`, C0
- Commit before: `4320614`
- Commit after: `4320614`
- Branch: `development`
- Worktree: clean except untracked `docs/dev/security_review_10JUN26/`
- Python: 3.13.7
- Optional/runtime packages: Pillow 12.2.0, pytest 9.0.2,
  smbprotocol installed, impacket installed, FastAPI 0.136.1, Uvicorn 0.46.0
- `xvfb-run`: `/usr/bin/xvfb-run`
- Exception inventory: 448 pass-only handlers across 103 product files
- Distribution: commands 6, experimental 33, GUI 375, shared 29, tools 5
- Candidate C1 source line counts:
  - `commands/http/verifier.py`: 246
  - `commands/http/operation.py`: 457
  - `shared/http_browser.py`: 358
  - `gui/utils/http_probe_runner.py`: 325
  - `gui/utils/protocol_extract_runner.py`: 834
  - `gui/browsers/http_browser.py`: 619
  - `experimental/se_dork/classifier.py`: 120
- Validation:
  - HTTP verifier/browser/extract baseline: 29 passed
  - SMB extract/quarantine baseline: 41 passed
  - Import/FTP/viewer baseline: 25 passed
- Automated: PASS
- Manual: n/a
- Residual risk: C1 caller and test-file scope remains subject to Claude's
  plan-only repo inspection and RA/HI review.
- Final status: ACCEPTED

## C1 Evidence

- Plan: `approved_plans/C1_http_redirect_policy.md`
- DA session: Claude implementation summary received 2026-06-10
- Commit before: `4320614`
- Commit after: C1 implementation commit (this commit)
- Files changed:
  - `shared/http_transport.py`
  - `shared/tests/test_http_transport.py`
  - `commands/http/verifier.py`
  - `shared/http_browser.py`
  - `gui/utils/protocol_extract_runner.py`
  - security-review planning and execution documents
- Line counts:
  - `shared/http_transport.py`: 333
  - `shared/tests/test_http_transport.py`: 936
  - `commands/http/verifier.py`: 243
  - `shared/http_browser.py`: 362
  - `gui/utils/protocol_extract_runner.py`: 820
- Commands:
  - `./venv/bin/python -m pytest shared/tests/test_http_transport.py -q`
  - focused HTTP operation, browser, probe, and extract pytest group
  - baseline extract/quarantine and import/FTP/viewer pytest groups
  - `python -m py_compile` for all changed Python modules
  - scoped bare-`urlopen` negative search
  - `git diff --check`
- Results:
  - transport tests: 54 passed
  - focused HTTP group including transport: 94 passed
  - remaining baseline groups: 66 passed
  - compile, static search, and whitespace checks: PASS
- RA findings:
  - Minor: Unicode IDNA names were normalized for comparison but written raw
    to the `Host` header, causing `UnicodeEncodeError` outside Latin-1.
  - Corrected directly by emitting canonical ASCII IDNA authority and Host
    values; added an across-redirect regression test.
  - Restored the tested private `_make_url()` browser compatibility helper
    after a cleanup attempt exposed its existing test contract.
- Manual test: n/a; local fixtures cover network behavior
- Residual risk:
  - C2 still owns recorded-IP pinning with hostname-based SNI/certificate
    identity and removal of browser hostname reconnection.
  - C3 still owns canonical TLS policy resolution.
- Final status: ACCEPTED

## C2 Evidence

- Plan: `approved_plans/C2_endpoint_pinning.md`
- DA session: Claude implementation summary received 2026-06-10
- Commit before: `834f893`
- Commit after: C2 implementation commit (this commit)
- Files changed:
  - `shared/http_transport.py`
  - `shared/http_browser.py`
  - `gui/utils/http_probe_runner.py`
  - `gui/utils/protocol_extract_runner.py`
  - focused transport, browser, probe, and extraction tests
  - `docs/TECHNICAL_REFERENCE.md`
  - approved C2 plan and execution documents
- Line counts:
  - `shared/http_transport.py`: 393
  - `shared/http_browser.py`: 362
  - `gui/utils/http_probe_runner.py`: 304
  - `gui/utils/protocol_extract_runner.py`: 798
  - `shared/tests/test_http_transport.py`: 1170
  - `gui/tests/test_http_probe.py`: 306
  - `gui/tests/test_protocol_extract_runner.py`: 326
  - `gui/tests/test_http_browser_window.py`: 439
  - `docs/TECHNICAL_REFERENCE.md`: 1454
- Commands:
  - `./venv/bin/python -m pytest shared/tests/test_http_transport.py -q`
  - focused HTTP browser, probe, extract, and endpoint pytest group
  - `./venv/bin/python -m pytest shared/tests/ gui/tests/ experimental/webui/tests/ -q`
  - exact daemon import test on current worktree and detached `834f893`
  - compile, static no-fallback searches, and `git diff --check`
  - local IPv6 HTTPS fixture with recorded-IP destination and hostname SNI
- Results:
  - transport tests: 61 passed
  - focused caller tests: 41 passed
  - broad suite: 3025 passed, 1 failed
  - broad failure:
    `experimental/webui/tests/test_daemon_cli.py::test_daemon_modules_import_without_tkinter`
  - the same failure reproduces from detached parent commit `834f893`; unrelated
    to C2
  - compile, static searches, whitespace, and IPv6 fixture: PASS
- RA findings:
  - No implementation defects.
  - Verified CPython 3.8 `urllib.request.HTTPSHandler` stores `_context`, and the
    custom connection avoids version-specific `HTTPSConnection.connect()`
    internals by calling `HTTPConnection.connect()` plus `SSLContext.wrap_socket()`.
- Manual test: n/a; local TLS/SNI/socket fixtures exercise the platform stack
- Residual risk:
  - Runtime support still includes EOL Python 3.8; compatibility was source
    verified because Python 3.8 is not installed in the review environment.
  - C3 still owns canonical TLS policy resolution and operator controls.
- Final status: ACCEPTED

## C3 Evidence

- Plan: `approved_plans/C3_http_tls_policy.md`
- DA session: Claude implementation summary received 2026-06-11
- Commit before: `fbbd502`
- Commit after: C3 implementation commit (this commit)
- Files changed:
  - canonical TLS policy and migration in `shared/config.py` and
    `shared/config_store.py`
  - HTTP discovery, browser, probe, extraction, dashboard, Server List,
    SearXNG, and Web UI post-scan consumers
  - unified/HTTP scan dialogs and App Config security UI
  - focused shared, GUI, SearXNG, and Web UI tests
  - approved C3 plan, lessons, and execution evidence
- Line counts:
  - `shared/config.py`: 949
  - `shared/config_store.py`: 581
  - `gui/components/app_config_dialog.py`: 1593
  - `gui/components/app_config_security_tab.py`: 195
  - `gui/components/dashboard_batch_ops.py`: 1515
  - `gui/dashboard/widget.py`: 1686
  - `experimental/se_dork/service.py`: 1217
  - `experimental/webui/post_scan_probe.py`: 276
- Commands:
  - all focused C3 pytest groups from the approved plan
  - `./venv/bin/python -m pytest`
  - `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`
  - `xvfb-run -a ./venv/bin/python -m pytest -m gui_smoke`
  - `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick --gui-smoke`
  - exact daemon import test in isolation
  - compile, static retired-key/hardcoded-policy searches, and
    `git diff --check`
- Results:
  - focused C3 groups: PASS
  - full suite: 3203 passed, 1 failed
  - broad failure:
    `experimental/webui/tests/test_daemon_cli.py::test_daemon_modules_import_without_tkinter`
  - the same broad import-order failure is documented from the C2 baseline;
    the exact test passes alone on C3 and on detached `fbbd502`
  - quick workflow: 60 passed
  - pytest `gui_smoke` marker gate: exit 5 because zero tests are marked;
    detached `fbbd502` has the same result
  - actual Xvfb GUI launch smoke: PASS; `./dirracuda --mock` remained running
    for 15 seconds
  - compile, static searches, and whitespace: PASS
- RA findings:
  - Medium: a real pre-modular install without the canonical key had repository
    defaults copied into `core.scan` before C3 checked explicit presence. The
    synthesized `true` was mistaken for a user value, so a saved legacy
    `false` was ignored and the marker was set.
  - Corrected directly by determining explicitness from the pre-modular
    runtime payload before default materialization. Added upgrade regressions
    for an existing keyless runtime config and for no runtime config.
  - Restored the missing approved Rev 6 plan artifact.
- Manual test:
  - PASS by HI on 2026-06-11: App Config default affected later HTTP
    browser/probe behavior, and an opposite transient scan choice did not
    rewrite the persisted default.
- Residual risk:
  - Already-modularized installs retain their synthesized canonical value by
    the approved canonical-present-wins rule.
  - App Config persists the composed `http` section, freezing current defaults
    into the owning shard.
  - The `pytest -m gui_smoke` final gate is presently nonfunctional because no
    tests use the marker; the repository's executable GUI smoke passed.
- Final status: ACCEPTED

## C4 Evidence

- Plan: `approved_plans/C4_smb_extract_containment.md` (Rev 3)
- DA session: Claude implementation summary received 2026-06-11
- Commit before: `8a3debc`
- Commit after: C4 implementation commit (this commit)
- Files changed:
  - `gui/utils/extract_runner.py`
  - `shared/quarantine_postprocess.py`
  - focused extraction and post-processing tests
  - approved C4 plan and execution evidence
- Line counts:
  - `gui/utils/extract_runner.py`: 917
  - `shared/quarantine_postprocess.py`: 49
  - `gui/tests/test_extract_runner_clamav.py`: 1091
  - `shared/tests/test_quarantine_postprocess.py`: 204
- Commands:
  - `./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py shared/tests/test_quarantine_postprocess.py -q`
  - focused extraction, promotion, browser, protocol, dashboard, and virtual-root
    regression group
  - independent label-map permutation and symlink-containment probes
  - `./venv/bin/python -m py_compile gui/utils/extract_runner.py shared/quarantine_postprocess.py`
  - `git diff --check`
- Results:
  - card-declared focused gate: 59 passed
  - broader touched-surface group: 177 passed
  - adversarial probes, compile, and whitespace checks: PASS
- RA findings:
  - No implementation defects.
  - Exact remote share identity remains on SMB calls and reports; deterministic
    safe labels alone drive local storage and promotion.
  - Empty relative paths and resolved symlink escapes are rejected before
    filesystem mutation or `getFile`.
  - DA report said the worktree was staged, while Git showed unstaged changes;
    this was a reporting-only discrepancy.
- Manual test: n/a; no live SMB target required
- Residual risk:
  - The containment check intentionally does not defend against concurrent
    local filesystem mutation between validation and open.
  - Browser promotion behavior is unchanged and continues to use the optional
    field's compatibility fallback.
- Final status: ACCEPTED

## C5 Evidence

- Plan: `approved_plans/C5_image_pixel_guard.md`
- DA session: Claude implementation summary received 2026-06-11
- Commit before: `c642457`
- Commit after: C5 implementation commit (this commit)
- Files changed:
  - `gui/components/image_viewer_window.py`
  - `gui/tests/test_image_viewer_window.py`
  - approved C5 plan and execution evidence
- Line counts:
  - `gui/components/image_viewer_window.py`: 215
  - `gui/tests/test_image_viewer_window.py`: 174
- Commands:
  - `./venv/bin/python -m pytest gui/tests/test_image_viewer_window.py -v`
  - locked C5 image-viewer, HTTP-browser, and FTP-browser pytest group
  - viewer keybinding and browser import-contract regressions
  - Python compile, Pillow threshold static search, and `git diff --check`
- Results:
  - focused C5 tests: 11 passed
  - locked C5 validation group: 43 passed
  - additional viewer/import regressions: 14 passed
  - compile, static search, and whitespace checks: PASS
- RA findings:
  - Minor: the approved plan artifact and tracker evidence were absent; restored
    during closeout.
  - Minor: `isinstance(value, int)` accepts booleans, so a boolean dimension
    could pass the malformed-input guard. Tightened the check to exact integers
    and added a rejection-before-load regression.
- Manual test:
  - PASS by HI on 2026-06-11: a normal browser image rendered successfully.
- Residual risk:
  - Pillow may reject extreme declared dimensions during `Image.open()` before
    the project limit runs; this is expected complementary protection.
  - The browser still holds the caller-provided compressed bytes and decoded
    image simultaneously, bounded by the existing byte and pixel limits.
- Final status: ACCEPTED
