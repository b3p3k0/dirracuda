# Validation Gates

Date: 2026-04-01

Use explicit completion labels:

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```

## Automated Gates

1. Syntax:
```bash
python3 -m py_compile gui/utils/extract_runner.py gui/components/dashboard.py gui/components/server_list_window/actions/batch.py
```

2. Targeted behavior tests:
```bash
./venv/bin/python -m pytest gui/tests/test_action_routing.py -q
./venv/bin/python -m pytest gui/tests/test_dashboard_bulk_ops.py -q
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py -q
```

3. Optional focused ClamAV/browser regression:
```bash
./venv/bin/python -m pytest gui/tests/test_browser_clamav.py gui/tests/test_clamav_results_dialog.py -q
```

## Manual Gates (HI)

1. SMB control check:
- Run batch extract on SMB row.
- Confirm behavior unchanged.

2. FTP positive path:
- Probe FTP row.
- Run batch extract.
- Confirm files downloaded and summary accurate.

3. HTTP positive path:
- Probe HTTP row.
- Run batch extract.
- Confirm files downloaded and summary accurate.

4. Missing snapshot negative path:
- Pick FTP/HTTP row with no probe snapshot.
- Run extract.
- Confirm deterministic `skipped` with clear note.

5. Dashboard post-scan bulk extract:
- Run an FTP scan with bulk extract enabled.
- Run an HTTP scan with bulk extract enabled.
- Confirm no SMB transport errors on F/H rows.

6. ClamAV integration parity (if enabled):
- Confirm per-file outcomes still route and summarize correctly for SMB/FTP/HTTP.
