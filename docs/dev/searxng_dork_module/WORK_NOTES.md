# C7 — DeGoog support and shared search naming

Status: complete. Automated validation and HI testing passed.
Commit authorized by HI after closeout; no push. Existing Reddit edits were preserved, including
README additions. No dependency, schema, migration, or auth changes.

## Issue and root cause

The configured DeGoog instance returned HTTP 404 for `/config`, so the existing
SearXNG-only preflight rejected it before search. Native `/api/search` already
returned JSON. It uses `page` rather than `pageno`, a ten-page limit, and
`source`/`sources` rather than `engine`/`engines`.

## Implemented

- Add a small `experimental/se_dork/backends.py` transport adapter.
- Detect DeGoog once using metadata after `/config` HTTP 404; explicit
  `/api/search` URLs skip SearXNG detection. Preserve proxy path prefixes.
- Keep normal runs free of extra upstream test searches. Explicit Test queries
  the detected backend and reports malformed payloads or access restrictions.
- Normalize DeGoog metadata before the unchanged store and classifier flow.
- Stop DeGoog pagination at ten pages; preserve SearXNG's forty-page cap.
- Rename desktop and Web UI feature labels to **Self-hosted Search**. Retain
  internal `se_dork` / `searxng` IDs, settings, provenance, filenames, and routes.
- Keep semantic log colors for both new and historical message prefixes.
- Update README, AGENTS, and the active workspace. Mark C1–C6 planning material
  as historical. Keep SearXNG configuration instructions provider-specific.

## Approved modularization and size exception

HI approved the proposed extraction and one-line test exception after reporting
manual testing passed.

- `docs/TECHNICAL_REFERENCE.md`: 1,712 → 1,656 lines (acceptable). Search
  runtime/flow detail now lives in `docs/SELF_HOSTED_SEARCH.md`, with links
  from the reference and README. The former runtime-section anchor is preserved.
- `gui/tests/test_experimental_features_dialog.py`: 2,347 → 2,347 lines
  (unacceptable by the normal rubric; explicit HI-approved exception). Only
  the expected tab label changed. New backend coverage stays in a separate file;
  splitting the existing test module remains outside this task.
- `.gitignore`: 147 → 148 lines (excellent). Added the new reference to the
  existing documentation allowlist so it is available for the eventual commit.

README reviewed at closeout: shared feature labels, DeGoog setup, provider limits,
and the extracted reference link match the implementation. Existing Reddit edits
remain intact.

## Validation

PASS — 601 targeted tests:

```bash
./venv/bin/python -m pytest shared/tests/test_se_dork*.py gui/tests/test_se_dork*.py gui/tests/test_dashboard_searxng_cancel.py gui/tests/test_dashboard_scan.py gui/tests/test_dashboard_provider_queue.py gui/tests/test_dashboard_experimental_sidecar.py gui/tests/test_scan_results_dialog.py gui/tests/test_unified_scan_dialog*.py gui/tests/test_scan_provider_options_searxng_scales.py gui/tests/test_log_semantic_color.py gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py experimental/webui/tests/test_searxng_routes.py experimental/webui/tests/test_pages.py -q --tb=short
```

PASS — 60 quick-lane tests and canonical desktop startup smoke:

```bash
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick --gui-smoke
```

The smoke harness launches `./dirracuda --mock` under Xvfb and confirms it stays
running for 15 seconds. This is startup validation, not a full visual review.

PASS — 102 Accessories tests after updating the approved label assertion:

```bash
./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q --tb=short
```

PASS — changed Python files compiled and whitespace checked:

```bash
./venv/bin/python - <<'PY'
from pathlib import Path
import py_compile, subprocess
files=subprocess.check_output(['git','diff','--name-only']).decode().splitlines()
files += ['experimental/se_dork/backends.py','shared/tests/test_se_dork_degoog.py']
for name in files:
 if name.endswith('.py'):
  py_compile.compile(name, doraise=True)
print('PASS: changed Python files compile')
PY
git diff --check
```

Optional JavaScript syntax check could not run: `node` is absent. The JS change
is one display string, reviewed in the diff. On a workstation with Node installed,
`node --check experimental/webui/static/searxng.js` should exit 0 with no output.
No package installation is needed for Dirracuda's functionality.

An initial test pass caught an accidental page-cap assignment in the cooldown
helper; it was removed and all retry tests passed on rerun. Subsequent failures
were old UI wording expectations; all are now updated and passing.
The Web UI tests emit an existing Starlette/httpx deprecation warning; dependencies
were left unchanged.

## Authorized live check

Baseline: original `run_preflight()` returned `instance_unreachable` for the
provided instance because `/config` returned 404. Native `/api/search` returned
HTTP 200 JSON without any server change.

PASS — new adapter preflight, pagination, snippets, and engine metadata:

```bash
./venv/bin/python - <<'PY'
from experimental.se_dork.client import run_preflight
from experimental.se_dork.service import _fetch_page
url='https://kevin-pc.banjo-tiaki.ts.net:8444/'
result=run_preflight(url, timeout=20)
print('preflight:', result)
assert result.ok and result.search_endpoint.endswith('/api/search')
seen=set()
for page in (1,2):
 rows,warnings=_fetch_page(result.search_endpoint, 'searxng documentation', page, timeout=20)
 urls={r['url'] for r in rows}
 print(f'page {page}: results={len(rows)}, new={len(urls-seen)}, sources={sorted({r["engine"] for r in rows})}')
 assert rows and all('content' in r and isinstance(r['engines'], list) for r in rows)
 seen.update(urls)
print('PASS: native JSON, pagination, metadata; no result hosts visited or DB opened.')
PY
```

Observed: page 1 returned 26 URLs (Brave Search/Wikipedia); page 2 returned
20 URLs, 19 new (Brave Search). Automated tests used mocked network and temporary
DBs. No live result host was visited and the user's DB was not opened by this check.

## HI check

PASS — HI reported manual testing passed before approving the final documentation
extraction. No additional HI test is needed for the documentation and one-line
assertion closeout. The separate live adapter check above did not visit result
hosts or open the user's DB.

## Lessons

- Similar result shapes do not mean identical APIs. Verify reachability routes,
  pagination parameters, page ceilings, and attribution fields independently.
- Adapt at the transport boundary; avoid renaming persisted IDs or changing
  schema when adding a backend to an existing workflow.
- A reachability check must not spend an extra upstream search per run.
- Provider detection should not hide 401/403/429 or connection failures by
  falling through to another provider.
- DeGoog's SearXNG response toggle is unnecessary for native JSON integration.
- Test both positive labels and their consumers: queue displays, result titles,
  and exact-prefix log coloring all depend on wording.
- Check documentation ignore rules when extracting a reference; a linked file
  must not be silently omitted from the eventual commit.
- Preserve historical planning as history; do not present its sidecar-only
  storage model as the current primary-DB contract.

Sources: [DeGoog API](https://degoog-org.github.io/docs/api.html),
[SearXNG API](https://docs.searxng.org/dev/search_api.html).

## Touched-file sizes

Counts are from the starting worktree, including existing README changes.
Files already modified for Reddit and left unchanged by this task are excluded.

| File | Before → after | Rating |
|---|---:|---|
| `.gitignore` | 147 → 148 | excellent |
| `docs/TECHNICAL_REFERENCE.md` | 1712 → 1656 | acceptable |
| `gui/tests/test_experimental_features_dialog.py` | 2347 → 2347 | unacceptable; HI-approved one-line exception |
| `AGENTS.md` | 167 → 167 | excellent |
| `README.md` | 782 → 797 | excellent |
| `docs/SELF_HOSTED_SEARCH.md` | 0 → 107 | excellent |
| `docs/dev/searxng_dork_module/ASCII_SKETCHES.md` | 96 → 99 | excellent |
| `docs/dev/searxng_dork_module/CLAUDE_PROMPTS.md` | 148 → 151 | excellent |
| `docs/dev/searxng_dork_module/OPEN_QUESTIONS.md` | 63 → 66 | excellent |
| `docs/dev/searxng_dork_module/README.md` | 93 → 87 | excellent |
| `docs/dev/searxng_dork_module/ROADMAP.md` | 83 → 91 | excellent |
| `docs/dev/searxng_dork_module/SPEC.md` | 160 → 54 | excellent |
| `docs/dev/searxng_dork_module/TASK_CARDS.md` | 324 → 337 | excellent |
| `experimental/se_dork/backends.py` | 0 → 50 | excellent |
| `experimental/se_dork/client.py` | 154 → 130 | excellent |
| `experimental/se_dork/models.py` | 80 → 81 | excellent |
| `experimental/se_dork/service.py` | 1217 → 1212 | good |
| `experimental/webui/app.py` | 1501 → 1501 | acceptable |
| `experimental/webui/static/searxng.js` | 250 → 250 | excellent |
| `experimental/webui/templates/base.html` | 80 → 80 | excellent |
| `experimental/webui/templates/scans.html` | 90 → 90 | excellent |
| `experimental/webui/templates/searxng.html` | 67 → 68 | excellent |
| `experimental/webui/tests/test_pages.py` | 572 → 572 | excellent |
| `gui/components/dashboard_experimental.py` | 430 → 430 | excellent |
| `gui/components/dashboard_provider_queue.py` | 407 → 407 | excellent |
| `gui/components/dashboard_scan_rollup.py` | 290 → 290 | excellent |
| `gui/components/dashboard_searxng_scan.py` | 400 → 400 | excellent |
| `gui/components/experimental_features/registry.py` | 82 → 82 | excellent |
| `gui/components/experimental_features/se_dork_tab.py` | 447 → 447 | excellent |
| `gui/components/log_semantic_color.py` | 161 → 161 | excellent |
| `gui/components/scan_provider_options.py` | 601 → 601 | excellent |
| `gui/components/scan_results_dialog.py` | 549 → 549 | excellent |
| `gui/components/se_dork_browser_window.py` | 1066 → 1066 | excellent |
| `gui/components/unified_scan_dialog.py` | 1210 → 1210 | good |
| `gui/components/unified_scan_layout.py` | 650 → 650 | excellent |
| `gui/dashboard/widget.py` | 1388 → 1388 | good |
| `gui/tests/test_dashboard_experimental_sidecar.py` | 216 → 216 | excellent |
| `gui/tests/test_dashboard_scan.py` | 1266 → 1266 | good |
| `gui/tests/test_log_semantic_color.py` | 567 → 580 | excellent |
| `gui/tests/test_scan_results_dialog.py` | 59 → 59 | excellent |
| `gui/tests/test_se_dork_browser_window.py` | 1004 → 1004 | excellent |
| `gui/tests/test_se_dork_tab.py` | 626 → 626 | excellent |
| `gui/tests/test_unified_scan_dialog.py` | 512 → 512 | excellent |
| `gui/tests/test_unified_scan_dialog_layout.py` | 475 → 475 | excellent |
| `shared/tests/test_se_dork_client.py` | 233 → 233 | excellent |
| `shared/tests/test_se_dork_degoog.py` | 0 → 162 | excellent |

This new work-notes file is also below 1,200 lines (excellent).
