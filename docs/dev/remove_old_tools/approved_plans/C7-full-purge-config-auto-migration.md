# C7 Approved Plan: Full Pry/RCE Artifact Purge + Legacy Config Auto-Migration

## Objective
Finish the sunset by removing non-runtime Pry/RCE artifacts and implementing assisted breaking cleanup for legacy top-level config keys (`pry`, `rce`) while preserving DB schema compatibility.

## Scope
- Delete dormant RCE signature package/data and dedicated tests.
- Remove `PyYAML` dependency and sync docs.
- Add startup config migration in `shared/config.py`:
  - detect top-level `pry`/`rce`
  - create timestamped backup
  - atomically rewrite config without those keys
  - log migration warning
  - if rewrite fails, continue with sanitized in-memory config and remediation warning
- Remove path-service fields/ops tied only to this subsystem.
- Keep DB migration/schema compatibility untouched.

## Files (high level)
- Runtime/config/path: `shared/config.py`, `shared/path_service.py`
- Artifact purge: `shared/signatures/rce_smb/*`, `conf/signatures/rce_smb/*.yaml`
- Dependency/docs: `requirements.txt`, `README.md`, `docs/TECHNICAL_REFERENCE.md`
- Tests: `shared/tests/test_config_legacy_key_migration.py`, `shared/tests/test_path_service_layout_v2.py`, delete `shared/tests/test_signature_loader_paths.py`
- Process docs: `docs/dev/remove_old_tools/{ROADMAP.md,TASK_CARDS.md,LESSONS_LEARNED.md}`

## Key Decisions
- Breaking cleanup accepted for legacy config keys.
- DB schema remains additive/compatible (no destructive migration in this card).
- Commit strategy preserved: C6 docs checkpoint first, then C7 cleanup commit.
