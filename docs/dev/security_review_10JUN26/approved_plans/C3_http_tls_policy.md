# C3 - Canonical HTTP TLS Policy: Approved Plan

Status: Rev 6 approved by HI and Codex RA on 2026-06-10

## Objective

- Make `http.verification.allow_insecure_tls` the sole persisted application
  default.
- Route HTTP browser, discovery, probe, extraction, and SearXNG target
  classification through one `SMBSeekConfig`-backed resolver.
- Make scan-dialog choices transient per-run overrides.
- Add the persisted default to App Config with a plain-language MITM warning.
- Consolidate legacy GUI values through a failure-safe one-time migration.

## Confirmed Root Cause

HTTP TLS behavior had multiple persisted GUI keys, direct JSON reads, and
hardcoded consumer values. Each surface evolved its own lookup rather than
using one persisted authority and resolver.

## Non-Goals

- Keep the default `true`; strict TLS by default is not part of C3.
- Do not change `verify_http` or `verify_https`.
- Do not change the subprocess temp-config override transport.
- Do not change public CLI behavior, dependencies, database schema, auth, CI,
  or the GUI-to-CLI boundary.
- Keep explicit keyword defaults on low-level HTTP helpers.
- Defer final operator documentation wording to C9.

## Exact Behavior

### Canonical Access

Add:

- `_coerce_tls_bool(value, default)` with explicit true/false recognition and
  malformed-value fallback.
- `SMBSeekConfig.get_http_allow_insecure_tls()`.
- `resolve_http_allow_insecure_tls(config_path=None)`.

Canonical paths use store mode and run migration. Non-canonical paths use
explicit-file mode and do not mutate the canonical store.

### One-Time Migration

Migration precedence:

1. An explicitly present canonical runtime value wins.
2. Otherwise use `unified_scan_dialog.allow_insecure_tls`.
3. Otherwise use `http_scan_dialog.allow_insecure_tls`.
4. Otherwise persist `true`.

Requirements:

- Run only in store mode before configuration load.
- Treat repository defaults as defaults, not explicit user choices.
- Normalize malformed `http.verification` before assignment.
- Abort without a marker on failed modularization or malformed owning shard.
- Store the durable marker in `conf.d/state/migration_flags.json`.
- Never raise into `SMBSeekConfig.__init__`.
- Leave retired GUI keys on disk but stop reading or writing them after
  migration.

### Runtime Propagation

- Discovery uses the typed config accessor.
- HTTP browser resolves once when opened.
- Generic HTTP probe dispatch accepts an optional override and resolves only
  when absent.
- Server List probe and extract launchers resolve once and pass one value to
  every worker.
- Dashboard post-scan probe and extract share one resolved per-run value.
- Web UI post-scan probe resolves once from the task config.
- SearXNG carries `RunOptions.allow_insecure_tls` through classification and
  probe; standalone runs resolve once.

### User Interfaces

- Unified and legacy HTTP scan dialogs initialize from the canonical default.
- Dialog persistence no longer writes either retired TLS key.
- Dialog result payloads retain the transient override.
- App Config adds an HTTP Target TLS control and writes the canonical key
  through the config abstraction.

## Expected Files

- `shared/config.py`
- `shared/config_store.py`
- `commands/http/operation.py`
- HTTP browser/probe/extract consumer modules
- Dashboard and Server List batch modules
- SearXNG and Web UI post-scan modules
- Unified/HTTP scan dialogs and App Config security UI
- Focused shared, GUI, SearXNG, and Web UI tests

## Edge Cases

- Explicit canonical `true` or `false` wins over stale GUI values.
- A pre-modular runtime file without the canonical key must not have a
  repository default mistaken for an explicit value.
- Missing legacy values persist the fallback `true`.
- Malformed values fall back safely.
- A malformed shard is not overwritten.
- Failed modularization is retried without a durable C3 marker.
- `False` overrides must not be lost through truthiness checks.
- Multi-target operations resolve once, not per worker or target.

## Validation

```bash
./venv/bin/python -m pytest shared/tests/test_http_tls_policy.py -q
./venv/bin/python -m pytest \
  shared/tests/test_config_validation_paths.py \
  shared/tests/test_config_store.py \
  shared/tests/test_config_legacy_key_migration.py \
  shared/tests/test_http_operation.py \
  shared/tests/test_se_dork_classifier.py -q
./venv/bin/python -m pytest \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_app_config_dialog_http_tls.py \
  gui/tests/test_scan_dialog_tls_policy.py \
  gui/tests/test_unified_scan_dialog.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_probe_cache_dispatch.py \
  gui/tests/test_protocol_extract_runner.py \
  gui/tests/test_action_routing.py \
  gui/tests/test_dashboard_bulk_ops.py -q
./venv/bin/python -m pytest \
  experimental/se_dork/tests \
  experimental/webui/tests/test_post_scan_probe_tls.py -q
./venv/bin/python -m py_compile \
  shared/config.py shared/config_store.py commands/http/operation.py \
  gui/browsers/http_browser.py gui/utils/probe_cache_dispatch.py \
  gui/components/dashboard_batch_ops.py \
  gui/components/server_list_window/actions/batch_operations.py \
  gui/components/server_list_window/actions/batch.py \
  gui/dashboard/widget.py experimental/webui/post_scan_probe.py \
  experimental/se_dork/classifier.py experimental/se_dork/service.py \
  experimental/se_dork/models.py experimental/se_dork/probe.py \
  gui/components/dashboard_searxng_scan.py \
  gui/components/unified_scan_dialog.py gui/components/http_scan_dialog.py \
  gui/components/app_config_dialog.py gui/components/app_config_security_tab.py
rg -n "allow_insecure_tls=True|allow_insecure_tls = True" \
  gui shared commands experimental
rg -n "unified_scan_dialog\.allow_insecure_tls|http_scan_dialog\.allow_insecure_tls" \
  gui shared commands experimental
git diff --check
```

Final gates remain the full pytest suite, quick agent workflow, GUI smoke suite,
and whitespace check.

## Line-Count Risk

`gui/dashboard/widget.py` began at 1684 lines. Only minimal pass-through
signature changes are permitted; stop if any touched file exceeds 1700 lines
without a separately approved modularization plan.

## Rollback

Revert only C3 files and remove the C3 marker if rollback occurs before
release. Stale GUI keys remain valid inert data for older versions.

## QA Addendum

During RA implementation review on 2026-06-11, a real pre-modular upgrade test
showed that modularization could synthesize the repository default before C3
checked whether the canonical value was explicit. RA corrected the migration
to inspect the pre-modular runtime payload first and added regression coverage.
This enforces the approved precedence without changing the card's product
decision.
