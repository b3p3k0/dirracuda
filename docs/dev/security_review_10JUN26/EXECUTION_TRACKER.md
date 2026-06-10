# Security Review Execution Tracker

Status: HI approved PA pack; Codex operating as RA; C2 accepted

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
| C3 | NOT STARTED |  | PENDING | PENDING |  | Canonical TLS policy |
| C4 | NOT STARTED |  | PENDING | PENDING |  | SMB extraction containment |
| C5 | NOT STARTED |  | PENDING | PENDING |  | Image pixel guard |
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
