# Web UI Validation Plan

Validation is card-scoped first, then widened when risk warrants.

## Command Discipline

When a command's exit status matters, do not pipe it through `grep`, `tail`, or
`tee` as the final command. If output needs capture, use:

```bash
./venv/bin/python -m pytest path/to/tests -q > /tmp/webui_test.txt 2>&1; RESULT=$?
cat /tmp/webui_test.txt
echo "pytest exit=${RESULT}"
```

When reporting test outcomes, report the exact pass/fail counts from the command
output you ran. Do not hard-code historical totals as a fixed expectation.

## Baseline

C0 records:

```bash
git status --short --branch
git ls-files '*.py' | xargs wc -l | sort -nr | head -20
which xvfb-run
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

## Focused Automated Gates

### Web Package

```bash
./venv/bin/python -m py_compile experimental/webui/__init__.py experimental/webui/app.py experimental/webui/server.py
./venv/bin/python -m pytest experimental/webui/tests -q
```

### Auth And Config

```bash
./venv/bin/python -m pytest \
  experimental/webui/tests/test_config.py \
  experimental/webui/tests/test_auth.py \
  experimental/webui/tests/test_sessions.py \
  experimental/webui/tests/test_csrf.py -q
```

### Scan Queue

```bash
./venv/bin/python -m pytest \
  experimental/webui/tests/test_tasks.py \
  experimental/webui/tests/test_scan_routes.py -q
```

### Results And Export

```bash
./venv/bin/python -m pytest \
  experimental/webui/tests/test_results.py \
  experimental/webui/tests/test_export.py -q
```

### Desktop Experimental Integration

```bash
xvfb-run -a ./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py -q
```

### Existing Server Ops Lane

```bash
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

## Manual HI Gates

Manual gates are required before calling the feature done.

### Desktop Control

1. Run `./dirracuda`.
2. Open `Experimental`.
3. Confirm tab order: `SearXNG`, `Reddit`, `Web UI`, `Dorkbook`, `Keymaster`.
4. Open the `Web UI` tab.
5. Confirm status text is clear and the configured URL is visible.
6. Confirm Start/Stop/Open in Browser/Copy URL controls are present and responsive.

### Local Web UI

1. Start localhost web UI:
   `./venv/bin/python -m experimental.webui.server`
   or
   `./venv/bin/uvicorn experimental.webui.app:create_app --factory`.
2. Open `http://127.0.0.1:2600`.
3. Confirm login is required.
4. Log in.
5. Navigate Dashboard, Scans, Results, Export, Config.
6. Queue a small scan or mock-safe scan.
7. Queue a scan with `Run probe on verified hosts after scan`.
8. Confirm host results show share/directory summary data when available.
9. Cancel a queued/running scan.
10. Export the database.
11. Log out and confirm protected pages are blocked.

### Mobile Web UI

1. Open the web UI at a phone-width viewport around 390px.
2. Confirm login, nav, scan queue, results, and config remain usable.
3. Confirm host/share summary cards do not require horizontal page scrolling.
4. Confirm buttons are tappable and status/error text remains visible.

### Remote Mode

Run only in a VM or isolated trusted network.

1. Configure non-loopback bind with TLS and allowlist.
2. Confirm TLS is enabled by default.
3. Confirm startup fails if allowlist is empty.
4. Confirm non-loopback with TLS disabled fails unless the explicit insecure
   override is set.
5. Confirm insecure override produces a clear warning.
6. Confirm allowed client can connect.
7. Confirm disallowed client is blocked.

## Documentation Gate

Every runtime behavior change must be reflected in:

- `README.md` for user-facing setup/usage.
- `docs/TECHNICAL_REFERENCE.md` for internals.
- `docs/dev/webui/LESSONS_LEARNED.md` for new pitfalls.
