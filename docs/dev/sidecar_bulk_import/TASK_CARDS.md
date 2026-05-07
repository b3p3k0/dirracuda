# Task Cards

## C1 - Promotion Core
- Add `promote_sidecar_prefills(...)` helper with best-effort summary and cancellation.
- Add helper-level unit tests.

Validation:
- `python3 -m py_compile gui/utils/sidecar_promotion.py`
- `./venv/bin/python -m pytest gui/tests/test_sidecar_promotion.py -q`

## C2 - Dashboard Wiring
- Add bulk callback factory and pass callback through SearXNG/Reddit window openers.
- Add wiring tests for callback presence and single refresh behavior.

Validation:
- `python3 -m py_compile gui/components/dashboard_experimental.py`
- `./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q`

## C3 - Browser Behavior
- Add multi-select dispatch for SearXNG and Reddit add-to-db actions.
- Implement background bulk workflow with progress/cancel + summary dialogs.
- Add Reddit right-click multi-selection parity fix.

Validation:
- `python3 -m py_compile gui/components/se_dork_browser_window.py gui/components/reddit_browser_window.py`
- `./venv/bin/python -m pytest gui/tests/test_se_dork_browser_window.py gui/tests/test_reddit_browser_window.py -q`
