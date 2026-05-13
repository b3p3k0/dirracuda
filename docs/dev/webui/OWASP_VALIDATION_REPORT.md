# OWASP Web UI Validation Report

**Date:** 2026-05-13
**Branch:** feature/secure-webui
**Commit under test:** fb13ab7 (webui(o4): harden credential store reads and sync security docs)
**Cards covered:** O1 – O4
**Validator:** DA O5

---

## Scope

Validates that O1–O4 webui security changes (auth anti-automation lockout, password policy,
strict CSP + security headers, credential store hardening) are test-covered, gate-passing,
and accurately reflected in documentation.

---

## Required Gates

### Gate 1 — Web UI test suite

```
./venv/bin/python -m pytest experimental/webui/tests -q
```

| Run | Result | Count | Duration |
|-----|--------|-------|----------|
| G1 | **PASS** | 324/324 | 33.9s |

### Gate 2 — GUI experimental features dialog

```
xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_experimental_features_dialog.py -q
```

| Run | Result | Count | Duration |
|-----|--------|-------|----------|
| G2 | **PASS** | 70/70 | 0.18s |

### Gate 3 — Agent testing workflow (quick lane)

```
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

| Run | Result | Count | Notes |
|-----|--------|-------|-------|
| G3 (pre-fix) | **FAIL** | 53/54 | Pre-existing regression — see below |
| G3 (post-fix) | **PASS** | 54/54 | 0.83s |

---

## Pre-existing Regression: test_s10

**Test:** `gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success`

**Introduced by:** commit `616c25b` (Carry sidecar probe snapshots into promotion) — prior to O1 work, unrelated to the webui security wave.

**Root cause:** `gui/components/se_dork_browser_window.py:603` reads `outcome.probe_snapshot_payload` (added in `616c25b`). The test stubs `outcome` as `types.SimpleNamespace` without that field. The resulting `AttributeError` is caught by the outer `except Exception` block, short-circuiting before `conn.commit()`. Assertion `fake_conn.commit_calls == 1` fails (actual: 0).

**Side effect on test_s11:** That test also lacked the field, but accidentally passed because its failure path (`RuntimeError` from the mocked `update_result_probe`) was triggered by the wrong exception. Fixing both stubs ensures test_s11 fails at the intended point.

**Fix applied:** Added `probe_snapshot_payload=None` to both `outcome` SimpleNamespace stubs in `gui/tests/test_server_ops_scenario_matrix.py` (test_s10 and test_s11). Two one-line additions, no production code changes. Aligns stubs with `ProbeOutcome` dataclass (`probe_snapshot_payload: Optional[dict] = None`).

---

## Documentation Drift Check

| File | Status |
|------|--------|
| `README.md` lines 481–521 | No drift — O1/O2/O3/O4 coverage present and accurate |
| `docs/TECHNICAL_REFERENCE.md` §7.6 (lines 1221–1240) | No drift — O1–O4 sections present and accurate |
| `docs/dev/webui/SECURITY_MODEL.md` | No drift — reviewed, consistent with implementation |
| `docs/dev/webui/OWASP_WAIVER_REGISTER.md` | Current — W-001..W-005 reviewed below |

---

## Waiver Summary

| ID | Scope | Decision | Review Date |
|----|-------|----------|-------------|
| W-001 | MFA for web UI auth | WAIVE | 2026-08-15 |
| W-002 | Credential store keychain/encryption migration | DEFER | 2026-08-15 |
| W-003 | Concurrent session management UI | DEFER | 2026-08-15 |
| W-004 | Session persistence across restart | DEFER | 2026-08-15 |
| W-005 | ASVS V6.2.12 breached-password checking | DEFER | 2026-08-15 |

All waivers have compensating controls documented in `OWASP_WAIVER_REGISTER.md`. Waiver set is current as of O5; W-005 was added in O2 and W-002 was updated in O4. Next review: 2026-08-15 or at next auth-scope expansion.

---

## Summary

All three required gates pass. One pre-existing test fixture regression (unrelated to O1–O4)
was identified, root-caused, and fixed with a minimal two-line stub alignment. No production
behavior was changed. Documentation is accurate and up to date.
