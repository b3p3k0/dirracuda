# Censys Integration Validation Plan

Date: 2026-05-14
Scope: C0-C9 card execution support

## Principles

1. Run targeted checks for touched components first.
2. Expand to wider regression only when card risk warrants it.
3. Report exact command lines and PASS/FAIL status.
4. Do not claim manual validation without operator evidence.

## Global Command Baseline

```bash
# Compile touched Python modules
./venv/bin/python -m py_compile <file1.py> <file2.py>

# Targeted tests
./venv/bin/python -m pytest <test_path_1> <test_path_2> -q

# Optional broader lane when risk warrants
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

## Card-by-Card Validation

## C0

```bash
rg -n "censys|Censys|platform" docs/dev/censys_integration docs/dev/censys_integration/INITIAL_PLANNING
rg -n "experimental_features|registry|dashboard_experimental" gui/components -g '*.py'
```

## C1

```bash
rg -n "example.com|https://github.com//|PLACEHOLDER|TBD" docs/dev/censys_integration
rg -n "https://docs.censys.com|raw.githubusercontent.com/b3p3k0" docs/dev/censys_integration
```

## C2

```bash
./venv/bin/python -m py_compile \
  gui/components/experimental_features/censys_discovery_tab.py \
  gui/components/experimental_features/registry.py
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

## C3

```bash
./venv/bin/python -m py_compile shared/config.py
./venv/bin/python -m pytest \
  shared/tests/test_config_validation_paths.py \
  shared/tests/test_censys_config_contract.py -q
```

## C4

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/models.py \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/client.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_query_builder.py \
  shared/tests/test_censys_client.py -q
```

## C5

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/store.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_store.py \
  shared/tests/test_censys_service_ftp.py -q
```

## C6

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_service_http.py \
  shared/tests/test_censys_service_ftp.py -q
```

## C7

```bash
./venv/bin/python -m py_compile \
  experimental/censys_discovery/query_builder.py \
  experimental/censys_discovery/service.py
./venv/bin/python -m pytest \
  shared/tests/test_censys_service_smb.py \
  shared/tests/test_censys_service_http.py \
  shared/tests/test_censys_service_ftp.py -q
```

## C8

```bash
./venv/bin/python -m py_compile \
  gui/components/censys_browser_window.py \
  gui/components/experimental_features/censys_discovery_tab.py \
  gui/components/dashboard_experimental.py
./venv/bin/python -m pytest \
  gui/tests/test_censys_browser_window.py \
  gui/tests/test_experimental_features_dialog.py -q
```

## C9

```bash
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_censys_browser_window.py \
  shared/tests/test_censys_client.py \
  shared/tests/test_censys_store.py -q
rg -n "Censys Discovery|censys_discovery|censys\.personal_access_token|censys\.credit_profile" \
  README.md docs/TECHNICAL_REFERENCE.md docs/dev/censys_integration/
```

## Manual HI Checks (Minimum)

1. C2: Confirm tab visibility and no breakage of existing experimental tabs.
2. C5-C7: Run one protocol job each and confirm persisted result rows.
3. C8: Promote one row and one batch; confirm main DB updates.
4. C9: Confirm estimate/live credit panel and docs click-path parity.

## Evidence Block Template

```text
AUTOMATED: PASS | FAIL
MANUAL:    PASS | FAIL | PENDING
OVERALL:   PASS | FAIL | PENDING
```
