# Plan: F0 — Full Migration Plan + Contract Inventory (Artifacts Only)

## Context

Full Dirracuda rebrand approved. The 2026-03-24 rebrand commit updated GUI headers and module docstrings but left config keys, user-data paths, DB filenames, env vars, and dialog internals still using SMBSeek naming. F0 produces the planning artifacts that will guide F1–F6 implementation cards. No runtime code changes in this card.

## Deliverables

Three artifacts created in `docs/dev/rebrand/`:

### 1. `NAMING_CONTRACT_MATRIX.md`
Complete KEEP/REPLACE/ALIAS/REVIEW inventory across 12 categories:
- Launchers and CLI flags
- Config file keys (`gui_app.smbseek_path`, `database.path`, quarantine paths, github_repo URL)
- User data paths (`~/.smbseek/*` → `~/.dirracuda/*`)
- DB filename (`smbseek.db` → `dirracuda.db`)
- DB metadata values (`tool_name`, `scan_type`)
- Env vars (`XSMBSEEK_*`)
- Internal logger name (`smbseek_gui`)
- Internal Python identifiers (class/method names)
- User-facing text (dialogs, error messages, output)
- GitHub URLs
- Test fixtures (KEEP to protect legacy compat regression coverage)

Key decisions encoded:
- `cli/smbseek.py`, `cli/ftpseek.py`, `cli/httpseek.py` → **KEEP** (per CLAUDE.md)
- `SMBSeekConfig`, `SMBSeekOutput`, `XSMBSeekConfig` class names → **KEEP** (internal per CLAUDE.md)
- `xsmbseek` launcher → **ALIAS** (keep working; add `dirracuda` wrapper)
- `~/.smbseek/` → **ALIAS** (bridge, not hard cut)
- `smbseek.db` → **ALIAS** (detect and fall back, no auto-rename)
- `XSMBSEEK_DEBUG_*` → **ALIAS** (honor both env var forms)
- `GUI_LOGGER_NAME = "smbseek_gui"` → **REPLACE** (internal, zero external consumers)
- 5 items explicitly tagged **REVIEW** requiring HI decision (F5 scope)

### 2. `F0_MIGRATION_BLUEPRINT.md`
Phased execution plan aligned to TASK_CARDS F1–F6:

**F1** (bridge, no visible change): env var aliases, `--backend-path` argparse alias, `gui_app.backend_path` config key alias, `settings_manager.set_backend_paths()` alias, DB detection dual-check.

**F2** (UI + launcher): all residual "SMBSeek" dialog text, version string, logger name, GitHub URLs, new `dirracuda` wrapper script.

**F3** (user data paths): startup migration routine (`~/.smbseek` → `~/.dirracuda`), all 10 path constants updated, fallback read policy.

**F4** (DB filename): detection priority order (dirracuda.db first, smbseek.db fallback), config defaults switch, `shared/output.py:131` dynamic path fix.

**F5** (metadata labels — gated on HI sign-off): GUI session `tool_name='dirracuda'`, query IN-list expansion, conditional DB default change.

**F6** (validation + rollback drill): F6_VALIDATION_REPORT.md + F6_ROLLBACK_RUNBOOK.md.

Includes: compatibility bridge design, cutover order, schema/data safety section (runtime-state guards, idempotency strategy, no-split-brain policy).

### 3. `F0_RISK_REGISTER.md`
15 identified risks (R1–R15, severity HIGH/MEDIUM/LOW), including:
- HIGH: Parser-coupled output `smbseek.db` text in `shared/output.py:131`
- HIGH: DB auto-detect only checks `smbseek.db` (must bridge in F1, switch in F4)
- HIGH: `smbseek_path` config key must have alias before any rename (F1 prerequisite)
- HIGH: `scan_sessions` column default change breaks legacy DB opens without compat queries

Rollback sequence per phase (exact git commands). Manual HI gates (14 total, non-negotiable). 6 open questions for HI before F5 begins.

## Files Changed (Artifacts Only)

- `docs/dev/rebrand/NAMING_CONTRACT_MATRIX.md` — created
- `docs/dev/rebrand/F0_MIGRATION_BLUEPRINT.md` — created
- `docs/dev/rebrand/F0_RISK_REGISTER.md` — created

## Validation

```bash
# Naming inventory (confirm baseline state before F1)
rg -n -i "\b(dirracuda|smbseek|xsmbseek)\b" xsmbseek gui shared cli tools conf

# Config key / path / env var inventory
rg -n -i "smbseek_path|\.smbseek|smbseek\.db|XSMBSEEK_|tool_name|scan_type" \
    xsmbseek gui shared cli tools conf

# Verify artifact files exist and are non-empty
ls -la docs/dev/rebrand/NAMING_CONTRACT_MATRIX.md \
        docs/dev/rebrand/F0_MIGRATION_BLUEPRINT.md \
        docs/dev/rebrand/F0_RISK_REGISTER.md
```

HI test needed: No — review artifacts, confirm open questions answered, then approve F1.
