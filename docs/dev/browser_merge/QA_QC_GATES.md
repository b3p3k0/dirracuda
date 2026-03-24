# QA/QC Gates - Browser Merge

Use after each card before marking a slice done.

Completion labels:

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Gate A: Routing Correctness

1. Browse launch still routes by protocol correctly.
2. No protocol cross-launch errors (`F` does not open SMB flow, etc.).
3. No duplicate-IP cross-protocol contamination.

## Gate B: Browser Parity

1. Directory navigation works.
2. File view works for text and image types.
3. Download writes only to quarantine path.
4. Cancel remains responsive.

## Gate C: Protocol-Specific Semantics

1. SMB virtual-root share flow is correct:
   - no share dropdown
   - top-level share listing appears first
   - share credentials/auth behavior remains correct after entering a share
2. FTP anonymous behavior preserved.
3. HTTP scheme/path handling preserved.
4. Banner panel parity:
   - SMB/FTP/HTTP all show consistent banner panel UX.
   - SMB banner is Shodan-derived when available, with explicit fallback text otherwise.

## Gate D: Probe Snapshot Integrity

1. Probe load/run still works from browser context.
2. Snapshot rendering in details remains correct.
3. Error payload shape remains compatible with renderers.

## Gate E: Regression Baseline

1. No SMB/FTP/HTTP browse regressions.
2. Action routing tests pass.
3. No new known failures introduced.

## Suggested Targeted Validation Commands

Run from repo root:

```bash
xvfb-run -a python -m pytest gui/tests/test_action_routing.py -v
xvfb-run -a python -m pytest gui/tests/test_ftp_browser_window.py -v
xvfb-run -a python -m pytest gui/tests/test_http_browser_window.py -v
xvfb-run -a python -m pytest gui/tests/test_ftp_probe.py gui/tests/test_http_probe.py -v
```

SMB-focused checks when SMB card is active:

```bash
xvfb-run -a python -m pytest gui/tests/test_server_list_card4.py gui/tests/test_probe_runner_subdirectories.py -v
```

Manual smoke checklist (Gate B):
1. Open browse for one `S`, one `F`, one `H` row.
2. Navigate into a directory and back out.
3. View one text file and one image where available.
4. Download one file and verify quarantine path.
5. Cancel an in-flight list/download operation.
6. Confirm banner panel text in all three modes:
   - FTP and HTTP still show existing banner/title behavior.
   - SMB now shows Shodan-derived banner text (or documented fallback placeholder).
7. SMB-specific root parity:
   - SMB opens at share list root (no dropdown)
   - entering share works
   - Up from share root returns to share list
   - share-open failure path is non-fatal
