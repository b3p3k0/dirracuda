# Security Review Validation Plan

Status: locked minimum gates

## Principles

- No live Shodan, Censys, SMB, FTP, HTTP, or internet targets.
- Use local fakes, monkeypatch, socket fixtures, and temporary directories.
- Test the negative security property, not only successful behavior.
- A test that merely asserts a helper was called is insufficient when the
  contract concerns destination identity, containment, or decode ordering.
- Exact commands and results are recorded in `EXECUTION_TRACKER.md`.

## Card Validation Matrix

| Area | Required adversarial scenarios |
|---|---|
| Redirects | relative redirect; same-origin absolute; scheme change; host case/IDNA; default-port equivalence; explicit port change; scheme-relative authority change; userinfo; malformed Location; fourth hop |
| Proxy bypass | `HTTP_PROXY`, `HTTPS_PROXY`, and lowercase variants point to a failing sentinel; target request still uses fixture destination |
| Endpoint pinning | DNS returns different address; socket destination remains recorded IP; Host and SNI remain saved hostname; failed IP does not retry hostname |
| TLS | strict chain/hostname path; insecure path; App Config default; transient run override; legacy-key migration; browser/probe/extract consistency |
| SMB extract | traversal share; slash/backslash; absolute path; dot-only; collisions; symlink parent; original remote share preserved |
| Image | over limit rejected before `load`; exact limit accepted; malformed dimensions; corrupt image |
| ZIP | 33 members; 256 MiB total boundary; 128 MiB selected boundary; streamed overrun; nested payload; encrypted; duplicate; JSON preference; valid CSV fallback |
| FTP | CR, LF, NUL, other C0, DEL; command method not called; normal Unicode path |
| SMB basename | Windows separators on POSIX; root-only; structured mode unchanged |
| Exceptions | intentional-silent unchanged; debug log sanitized; surfaced failure reaches owner contract; no hot-loop log flood |

## Baseline Targeted Gates

```bash
./venv/bin/python -m pytest \
  shared/tests/test_http_operation.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_protocol_extract_runner.py -q

./venv/bin/python -m pytest \
  gui/tests/test_extract_runner_clamav.py \
  shared/tests/test_quarantine_postprocess.py -q

./venv/bin/python -m pytest \
  gui/tests/test_data_import_engine.py \
  gui/tests/test_ftp_browser.py \
  gui/tests/test_browser_viewer_keybindings.py -q
```

PA baseline result: 29 + 41 + 25 = 95 passed.

## Per-Card Gates

Each card runs:

1. New focused tests.
2. Existing tests for every touched module.
3. `python -m py_compile` for added or heavily edited modules when useful.
4. `git diff --check`.
5. Required static negative searches from the task card.

Exception batches additionally run the quick agent workflow because broad
catch behavior frequently affects startup and GUI orchestration.

## Final Automated Gates

Run from repo root:

```bash
./venv/bin/python -m pytest
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
xvfb-run -a ./venv/bin/python -m pytest -m gui_smoke
git diff --check
```

If `xvfb-run` is unavailable, record the blocker and exact install/unblock
command. The GUI smoke gate remains `PENDING`, not passed.

## Final Static Checks

```bash
rg -n "urllib\\.request\\.urlopen" \
  commands/http shared/http_browser.py gui/utils/http_probe_runner.py \
  gui/utils/protocol_extract_runner.py

rg -n "extractall" gui/utils/data_import_engine.py

rg -n "allow_insecure_tls=True|allow_insecure_tls = True" \
  gui shared commands experimental

rg -n "conf/config\\.json|Path\\(config_path\\).*read_text" \
  gui shared commands experimental
```

Matches are reviewed, not blindly required to be zero. Any allowed match must
be explained in the validation report.

## Manual HI Gates

1. **TLS source of truth**
   - Change HTTP TLS policy in App Config.
   - Reopen browser/probe/extract surfaces.
   - Confirm they use the new default.
   - Run one scan with the opposite transient choice.
   - Confirm persisted App Config value did not change.
2. **Image**
   - Open a normal image from a mocked/local browser target.
3. **ZIP**
   - Validate/import one normal Dirracuda ZIP export.
4. **GUI regression**
   - Launch only through `./dirracuda`.
   - Open Start Scan, App Config, Server List, and protocol browsers.
   - Confirm no pop-under, teardown, or obvious logging regressions.

## Result Semantics

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

`OVERALL: PASS` requires both automated and required manual gates to pass.
