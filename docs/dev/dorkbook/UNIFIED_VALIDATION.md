# Unified Dorkbook validation

Date: 2026-09-27
Status: implementation complete; HI acceptance pending.
The [Start Scan follow-up](START_SCAN_DORKBOOK.md) retires the separate Shodan
editor. Commands below record the earlier implementation snapshot; use the
follow-up checks for the current launch paths.

## Outcome

U1–U6 are implemented: one provider-grouped desktop/Web library, four persisted
search destinations, explicit Apply to Search, 52 curated built-ins, contextual
access, and consistent dork terminology. Original broad Shodan queries/keys and
custom dorks survive upgrade. The original U1 storage checkpoint is `e8d9a6d`;
its [migration and recovery evidence](U1_VALIDATION.md) remains applicable.

The root cause was separate provider-specific query storage/presentation left
over from Self-hosted Search's earlier accessory status. The new shared default
service retains Shodan config paths and owns `se_dork.default_query`. A nonblank
legacy desktop web query imports only when that key is absent. Manual web-search
text is run-local; Apply persists. Cached GUI preference saves preserve the
current canonical default. Strict configuration loading rejects malformed
storage before fallback/materialization or overwrite; explicit-file saves use
atomic replacement. The GUI-to-CLI subprocess boundary is unchanged.

Desktop explicit Apply updates the matching open field; focus refresh preserves
manual edits. Browser tabs use ephemeral BroadcastChannel messages for explicit
Apply, with clean-field focus refresh as an older-browser fallback. No query is
persisted to browser storage by Dorkbook. Selection and double-click only preview.
Editing/deleting custom dorks leaves their previously applied query unchanged.

AUTOMATED: PASS
LIVE API: reachable and valid JSON; upstream limited, useful yield unverified
MANUAL: PENDING
OVERALL: ready for HI acceptance

## Automated checks

Final regression command — **1,006 passed**, one existing Starlette/httpx
deprecation warning (no dependency changes):

```bash
xvfb-run -a ./venv/bin/python -m pytest \
  shared/tests/test_dorkbook*.py shared/tests/test_config*.py \
  shared/tests/test_censys_config_contract.py shared/tests/test_http_query_config.py \
  shared/tests/test_ftp_config.py gui/tests/test_dorkbook*.py \
  gui/tests/test_discovery_dork_config.py gui/tests/test_scan_dork_editor_dialog.py \
  gui/tests/test_unified_scan_dialog*.py \
  gui/tests/test_scan_provider_options_searxng_scales.py \
  gui/tests/test_config_save_reconciliation.py gui/tests/test_app_config_dialog*.py \
  gui/tests/test_messagebox_guardrail.py gui/tests/test_theme_style_guardrail.py \
  experimental/webui/tests -q
```

An additional canonical-shard restart/cached-preference regression was added
after the broad run began; its final targeted suite passed **29 tests**:

```bash
./venv/bin/python -m pytest shared/tests/test_dorkbook_defaults.py -q
```

The initial broad run reported 1,004 passes and one failure in the existing
headless-daemon import test. GUI tests had already imported Tkinter into the
same process. Running that test alone passed; it now checks daemon imports in
a fresh subprocess, so prior GUI imports cannot cause a false failure and
cached daemon modules cannot cause a false pass. The final broad run above
includes that correction.

Coverage includes real legacy/fresh sidecars, rollback/consistent backups,
provider uniqueness/custom collisions, original catalog keys, all selected
ideas, four independent defaults, failure handling, strict malformed-data
checks, stale UI/preferences behavior, auth/CSRF/origin protection, and template
layout. Real Tk tests use Xvfb and disposable state. QuickJS tests exercise
preview-only selection, copying, default refresh races, and ephemeral Apply
messages. Network calls in automated tests are mocked.

Compilation — **PASS, 35 changed Python files**:

```bash
./venv/bin/python - <<'PY'
import py_compile, subprocess
files = set(subprocess.check_output(['git','diff','--name-only'], text=True).splitlines())
files.update(subprocess.check_output(['git','ls-files','--others','--exclude-standard'], text=True).splitlines())
python_files = sorted(p for p in files if p.endswith('.py'))
for path in python_files:
    py_compile.compile(path, doraise=True)
print(f'PASS: compiled {len(python_files)} changed Python files')
PY
git diff --check
```

Whitespace check: PASS. The compilation enumeration was run before commit.
README and Technical Reference were reviewed/updated against the final code;
`img/dorkbook.png` is a real Tk screenshot using disposable sample state.

## Live checks

Both owned aggregators answered metadata requests and all 25 shipped web dorks,
plus page two of the broad default, with HTTP 200/results lists. SearXNG reported
rate limits/CAPTCHAs and returned zero results. DeGoog returned 0–15 results,
from Wikipedia; a diagnostic exposed Brave HTTP 429. Those counts are not
verified directory yield. No result URLs were followed, downloads made, or
primary database rows created. No paid Shodan calls were made.

See [per-query evidence, exact command forms, harness correction, and retry
steps](LIVE_BACKEND_CHECKS.md). Upstream recovery is needed for useful live-yield
assessment, not for library/default HI acceptance. The committed opt-in harness
stops on upstream throttling and has mocked regression coverage.

## HI test needed: yes

1. Launch **`./dirracuda`**. Open Accessories → Dorkbook. Confirm the upgraded
   library opens, your old customs remain, and both provider groups are visible
   through scrolling/jump controls. Built-ins are italic and cannot be changed.
2. Filter Books, search EPUB, and select a row. Confirm full query/notes and
   destination preview. Selection/double-click must not move the default mark.
3. Keep Start New Scan open. Use either provider's Dorkbook button and apply
   one dork to each Shodan protocol and one to Self-hosted Search. Confirm only
   the matching default/input changes; no provider toggle, result cap, or scan
   changes. Both buttons must reuse the same library window.
4. Type a temporary manual Self-hosted Search query. Switch window focus:
   preserve that text. Explicitly Apply another web dork: replace that field.
   Close/reopen and restart Dirracuda: load the applied default again.
5. Add a custom dork for each provider. Check Copy Query, edit, and delete with
   and without session confirmation. Editing/deleting an applied custom must
   leave the saved search query intact until another Apply.
6. Open Web UI → Extras → Dorkbook. Check groups, topic/text filtering, preview,
   Copy Query, and provider-aware Add/Delete. In another tab keep Self-hosted
   Search open; Apply a web dork and confirm the matching input updates. Desktop
   should read it on refocus/reopen. Repeat desktop Apply → browser refocus with
   a clean field; manual input must remain unless explicitly replaced there.
7. Confirm a chosen dork reaches the search request when you run a search.
   For Shodan this may spend query credits, so choose your normal test budget.
   Web-search results may remain empty/limited until upstream engines recover.

The real user sidecar migration and interactive browser layout/restart checks
remain HI-owned. Tests never migrated the user's real sidecar. 33C Caddy/custom
listing exploration, Calibre/OPDS application support, and Reddit saved dorks
remain deferred. Migration backups are retained, not pruned as dead files.

## File sizes

Before is the U1 checkpoint (`e8d9a6d`); new files start at zero. No touched text
file exceeds 1,700 lines. Technical Reference remains acceptable; Web app and
unified scan dialog are good; other touched files are excellent. The Web routes
were extracted and the desktop library simplified rather than growing large
entrypoint files. Binary screenshot sizes use bytes rather than line counts.

| File | Before | After | Rating |
|---|---:|---:|---|
| `AGENTS.md` | 170 | 170 | excellent |
| `README.md` | 790 | 803 | excellent |
| `docs/TECHNICAL_REFERENCE.md` | 1664 | 1667 | acceptable |
| `docs/dev/dorkbook/ASCII_SKETCHES.md` | 193 | 193 | excellent |
| `docs/dev/dorkbook/CANDIDATE_DORKS.md` | 96 | 97 | excellent |
| `docs/dev/dorkbook/CATALOG_RESEARCH.md` | 0 | 123 | excellent |
| `docs/dev/dorkbook/IMPLEMENTATION_PLAN.md` | 201 | 202 | excellent |
| `docs/dev/dorkbook/LESSONS_LEARNED.md` | 31 | 57 | excellent |
| `docs/dev/dorkbook/LIVE_BACKEND_CHECKS.md` | 0 | 85 | excellent |
| `docs/dev/dorkbook/OPEN_QUESTIONS.md` | 16 | 17 | excellent |
| `docs/dev/dorkbook/README.md` | 42 | 30 | excellent |
| `docs/dev/dorkbook/ROADMAP.md` | 82 | 82 | excellent |
| `docs/dev/dorkbook/SPEC.md` | 92 | 92 | excellent |
| `docs/dev/dorkbook/TASK_CARDS.md` | 235 | 234 | excellent |
| `docs/dev/dorkbook/UNIFIED_LIBRARY_PROPOSAL.md` | 117 | 117 | excellent |
| `docs/dev/dorkbook/UNIFIED_VALIDATION.md` | 0 | 206 | excellent |
| `docs/dev/dorkbook/VALIDATION_REPORT.md` | 94 | 94 | excellent |
| `experimental/dorkbook/catalog.py` | 0 | 110 | excellent |
| `experimental/dorkbook/defaults.py` | 0 | 122 | excellent |
| `experimental/dorkbook/models.py` | 87 | 142 | excellent |
| `experimental/webui/app.py` | 1501 | 1331 | good |
| `experimental/webui/dorkbook_routes.py` | 0 | 149 | excellent |
| `experimental/webui/experimental_models.py` | 302 | 316 | excellent |
| `experimental/webui/static/dorkbook.js` | 190 | 152 | excellent |
| `experimental/webui/static/dorkbook_defaults.js` | 0 | 38 | excellent |
| `experimental/webui/static/style.css` | 727 | 745 | excellent |
| `experimental/webui/templates/dorkbook.html` | 61 | 51 | excellent |
| `experimental/webui/templates/scans.html` | 90 | 91 | excellent |
| `experimental/webui/templates/searxng.html` | 68 | 70 | excellent |
| `experimental/webui/tests/test_daemon_cli.py` | 270 | 268 | excellent |
| `experimental/webui/tests/test_dorkbook_browser.py` | 0 | 138 | excellent |
| `experimental/webui/tests/test_dorkbook_routes.py` | 500 | 597 | excellent |
| `gui/components/discovery_dork_config.py` | 79 | 11 | excellent |
| `gui/components/dorkbook_events.py` | 0 | 61 | excellent |
| `gui/components/dorkbook_window.py` | 900 | 606 | excellent |
| `gui/components/experimental_features/dorkbook_tab.py` | 62 | 62 | excellent |
| `gui/components/scan_dork_editor_dialog.py` | 511 | 475 | excellent |
| `gui/components/scan_provider_options.py` | 601 | 603 | excellent |
| `gui/components/unified_scan_dialog.py` | 1197 | 1201 | good |
| `gui/components/unified_scan_layout.py` | 650 | 656 | excellent |
| `gui/tests/test_dorkbook_events.py` | 0 | 72 | excellent |
| `gui/tests/test_dorkbook_rendered.py` | 0 | 123 | excellent |
| `gui/tests/test_dorkbook_window.py` | 303 | 240 | excellent |
| `gui/tests/test_scan_dork_editor_dialog.py` | 302 | 248 | excellent |
| `gui/tests/test_unified_scan_dialog.py` | 512 | 518 | excellent |
| `gui/tests/test_unified_scan_dialog_layout.py` | 475 | 481 | excellent |
| `gui/tests/test_unified_scan_dialog_searxng_controls.py` | 387 | 393 | excellent |
| `gui/utils/settings_manager.py` | 1037 | 1044 | excellent |
| `img/dorkbook.png` | 27917 bytes | 103806 bytes | binary |
| `scripts/check_dorkbook_backends.py` | 0 | 110 | excellent |
| `shared/config.py` | 949 | 966 | excellent |
| `shared/config_store.py` | 583 | 604 | excellent |
| `shared/discovery_dork_config.py` | 0 | 79 | excellent |
| `shared/tests/test_dorkbook_backend_check.py` | 0 | 146 | excellent |
| `shared/tests/test_dorkbook_catalog.py` | 0 | 97 | excellent |
| `shared/tests/test_dorkbook_defaults.py` | 0 | 218 | excellent |
| `shared/tests/test_dorkbook_migration.py` | 268 | 270 | excellent |
| `shared/tests/test_dorkbook_providers.py` | 150 | 150 | excellent |
| `shared/tests/test_dorkbook_store.py` | 235 | 237 | excellent |
