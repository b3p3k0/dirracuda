# Approved Plan: C6 — Docs Sync + Lessons + Closeout

## Objective

Finalize documentation to match the post-sunset codebase (Pry removed in C2, RCE removed in C3). Replace all "suspended/incomplete" language with accurate "sunset/removed" language. Close out project tracking docs with execution report and final validation summary.

## Context

C1–C5 removed the Pry and RCE runtime pipelines, UI controls, scanner modules, and test fixture residue. The documentation still described these as suspended features that could be re-enabled. The following retained artifacts are documented as compatibility-only, not active runtime:

- `shared/signatures/rce_smb/loader.py` + `validator.py` — retained for historical/tooling/tests compatibility
- `conf/signatures/rce_smb/*.yaml` — retained as historical data artifacts
- `pry` / `rce` config keys — tolerated in old `config.json` files; no active runtime consumers
- `share_credentials` table — DB schema preserved; existing rows readable; no runtime writer

## Files Changed

| File | Change |
|------|--------|
| `README.md` | Line 82: PyYAML description updated; "active scanning" claim removed |
| `docs/TECHNICAL_REFERENCE.md` | 14 targeted edits (see execution report in TASK_CARDS.md) |
| `docs/dev/remove_old_tools/ROADMAP.md` | C1–C4 + C6 statuses → Done; M2–M4 milestones marked Done; C6 execution note added |
| `docs/dev/remove_old_tools/TASK_CARDS.md` | C6 execution report + final validation report appended |
| `docs/dev/remove_old_tools/LESSONS_LEARNED.md` | C5 follow-up resolved; C6 entry appended |
| `docs/dev/remove_old_tools/approved_plans/C6-docs-sync-closeout.md` | This file |

## Key Decisions

1. **PyYAML retained** — `shared/signatures/rce_smb/loader.py` is a legitimate consumer; dep description updated to say historical/tooling/tests compatibility, not active scanning.
2. **`pry`/`rce` config keys not removed** — existing installations parse these without error; removing the keys from parsing would break config loading for users with old configs. Marked as legacy-only in docs.
3. **Config table rows kept** — `rce` and `pry` rows remain in §3.1 table but are marked `_(legacy)_` so readers know they have no active runtime effect.
4. **Guardrail grep hit classification** — hits in TECHNICAL_REFERENCE.md are intentional sunset references (past-tense removal notes); zero unexpected active-runtime claims.

## Validation Evidence

- Guardrail grep on `README.md` + `docs/TECHNICAL_REFERENCE.md`: zero unexpected active-runtime claims
- Runtime code grep (`dirracuda`, `gui/`, `shared/`, `cli/`, `commands/`, `conf/`): fully clean
- `pytest gui/tests/test_action_routing.py`: 36 passed
- `pytest gui/tests/test_server_ops_scenario_matrix.py`: 12 passed, 1 pre-existing failure (`test_s10_se_dork_probe_task_lifecycle_success`) — confirmed pre-existing at C4 baseline
- `pytest shared/tests/test_ftp_state_tables.py`: 13 passed

## Security References

- OWASP Attack Surface Analysis Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Attack_Surface_Analysis_Cheat_Sheet.html
- CISA Product Security Bad Practices: https://www.cisa.gov/resources-tools/resources/product-security-bad-practices
- NIST SP 800-171r3 Least Functionality (03.04.06): https://nvlpubs.nist.gov/nistpubs/SpecialPublications/800-171r3/NIST.SP.800-171r3.html
