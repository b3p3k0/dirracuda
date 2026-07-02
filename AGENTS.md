# AGENTS.md

## What This Project Is

Dirracuda is a network discovery and audit toolkit for SMB, FTP, and HTTP open directory
listings. It combines Shodan/Censys-based host discovery with concurrent protocol
verification, a SQLite database, CLI workflows, and a Tkinter GUI.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp conf/config.json.example conf/config.json
```

Web UI requires extra deps:

```bash
pip install -r experimental/webui/requirements-web.txt
./venv/bin/python -c "from experimental.webui.auth import set_password; set_password('admin', 'your_password_here')"
```

## Key Commands

```bash
# GUI (always use this as the runtime entrypoint)
./dirracuda

# Tests
./venv/bin/python -m pytest
./venv/bin/python -m pytest --cov=shared --cov=gui

# CLI tools
./cli/smbseek.py --country US --verbose
./cli/ftpseek.py --country US
./cli/httpseek.py --country US

# Web UI
./venv/bin/python -m experimental.webui.server          # default: 127.0.0.1:2600
./venv/bin/python -m experimental.webui.server --host 0.0.0.0 --port 2600
```

> **Entrypoint guardrail**: `./dirracuda` is the only valid runtime entrypoint.
> `gui/main.py` is a legacy import shim — never use it as an implementation target.

## Architecture

### Layer Stack

```
GUI (gui/)            — Tkinter dashboard; spawns CLI as subprocesses, parses stdout
CLI (cli/)            — smbseek.py / ftpseek.py / httpseek.py (argument parsing only)
Workflow (shared/)    — UnifiedWorkflow / FtpWorkflow / HttpWorkflow (orchestration)
Commands (commands/)  — discover/, access/, ftp/, http/ (protocol-specific ops)
Shared (shared/)      — config, database, browsers, adapters, quarantine
DB (tools/)           — db_manager.py, db_schema.sql, db_migrations.py
```

### Critical Boundary: GUI → CLI

The GUI does **not** call `shared/workflow.py` directly. It spawns CLI scripts as
subprocesses and parses stdout. This boundary is intentional and must not be bypassed.
The owners are `gui/utils/backend_interface/interface.py` and `gui/utils/scan_manager.py`.

### SMB Discovery Pipeline

Two-stage flow in `shared/workflow.py::UnifiedWorkflow.run()`:

1. **Discovery**: Shodan query → host filter (rescan policy) → TCP port 445 check →
   auth probe (`shared/smb_adapter.py`)
2. **Access**: Share enumeration → accessibility test → file manifest → persist

### Dual SMB Backend

`shared/smb_adapter.py::SMBAdapter` wraps both:
- **smbprotocol** — primary; `require_signing=True`, no SMB1
- **impacket** — fallback; allows SMB1/unsigned, activated via `--legacy`

### Config Resolution Order (highest → lowest)

CLI args → `~/.dirracuda/gui_settings.json` → `conf/config.json` → hardcoded defaults

Always access config through `SMBSeekConfig` in `shared/config.py`. Never read
`conf/config.json` directly.

### Database

SQLite at `dirracuda.db` (path configurable). Schema migrations are additive and run on
startup via `shared/db_migrations.py`. Core tables: `smb_servers`, `ftp_servers`,
`http_servers`, `share_access`, `file_manifests`, `scan_sessions`, `failure_logs`,
`host_probe_cache`, `host_user_flags`, `sherlock_results`, `sherlock_hits`.

### User-Data Paths

Always call `shared/path_service.py::get_paths()` — returns a `DirracudaPaths`
dataclass with every canonical path under `~/.dirracuda`. Never construct these paths
by hand.

## GUI Conventions

| Rule | Detail |
|------|--------|
| Messageboxes | Always `gui.utils.safe_messagebox` — direct `tkinter.messagebox` is banned. Enforced by `test_messagebox_guardrail.py`. |
| Dialog focus | Call `gui.utils.dialog_helpers.ensure_dialog_focus(dialog, parent)` as the final step in any `Toplevel` that calls `grab_set()`. |
| Dialog teardown | Never destroy Tk dialogs from worker threads. The UI thread `after(...)` loop owns teardown. |
| Theming | Use `gui.utils.style.SMBSeekTheme.apply_to_widget(widget, style_name)` with named styles only. Raw style strings are banned. Enforced by `test_theme_style_guardrail.py`. |
| Component extraction | `DashboardWidget` methods are split into satellite modules. Each function takes `dash` as its first arg and routes messageboxes through `_mb()` so test monkeypatches intercept correctly. New extractions must follow this pattern. |

## Accessories (Experimental) Surfaces

All experimental features live under `experimental/`. Most use a sidecar SQLite DB under
`~/.dirracuda/data/experimental/`. Exceptions: **SearXNG Dork** (C9) and **Redseek** (C10)
write new runs directly to the primary `dirracuda.db`; their legacy sidecar data remains
available under Accessories → Legacy Sidecar Data for historical browsing.

GUI tabs for these surfaces live in `gui/components/experimental_features/`.

| Feature | Entry point | DB | Notes |
|---------|-------------|-----|-------|
| Web UI | `experimental/webui/server.py` | config/state | FastAPI companion; cookie sessions, CSRF protection; remote mode requires TLS + allowlist |
| Dorkbook | `experimental/dorkbook/store.py` | sidecar | Shodan query library; built-in read-only dorks + user customs |
| Keymaster | `experimental/keymaster/store.py` | sidecar | Multi-key API key store; selecting a key updates active Shodan key in memory |
| Redseek | `experimental/redseek/service.py` | **primary** | Reddit ingestion via anonymous RSS feed/search only; new runs write to primary DB, auto-sync SMB/FTP/HTTP targets; legacy data in `reddit_od.db` |
| SearXNG Dork | `experimental/se_dork/service.py` | **primary** | SearXNG-based dork search; two-commit transaction model; auto-sync to primary protocol tables |
| Censys Discovery | `experimental/censys_discovery/service.py` | sidecar | Censys Platform v3 alternative discovery; requires org-scoped PAT. **Suspended** — free-tier API does not provide candidate-list endpoints required for in-app runs. |
| Sherlock | `shared/sherlock/` + `gui/components/experimental_features/sherlock_tab.py` (pattern manager split into `sherlock_pattern_manager.py` / `sherlock_category_actions.py` / `sherlock_value_actions.py`) | **primary** (`sherlock_results`/`sherlock_hits`) | Display-only exposure triage over existing probe snapshot paths. Never downloads, reads content, authenticates, or probes. Pure matcher (purity-guarded); Server List `Risk` column + `Scan Sherlock` action; optional post-probe hook; read-only desktop/Web UI display (quiet — blank unless fresh positive finding). V2 adds optional User1/User2/User3 color tags, user-tag-then-severity row tint via `gui/utils/sherlock_risk_display.py`, and a probe-summary Risk column when post-probe Sherlock finds fresh risk. The `Manage Patterns...` modal is a two-pane category/value view: grouped value rows (`shared/sherlock/grouping.py`) are display-only over one `SherlockPattern` per pattern string; Add/Edit takes a comma-separated `Patterns` field (literal commas in a pattern unsupported); Tag Apply is custom-only (built-in `color_tag` not persisted). |

## Test Conventions

- Tests live in `shared/tests/`, `gui/tests/`, `experimental/webui/tests/`
- No shared `conftest.py` — fixtures are per-file
- Mock all external dependencies (Shodan, SMB adapters, Censys) via monkeypatch —
  never hit real network services
- Use `tempfile.mkstemp` or `tmp_path` — never `tempfile.mktemp`
- Pytest markers: `scenario`, `fuzz`, `fuzz_heavy`, `gui_smoke`

## Branch & CI

- Active development: `development` branch; production: `main`
- PRs `development → main` are gated by `scripts/check_branch_parity.py`
  (run in CI via `.github/workflows/promotion-parity.yml`)
- Non-docs commits unique to either branch require a disposition line in the PR body:
  `ported`, `superseded`, or `intentionally dropped`
- Never commit directly to `main`

## Behavioral Contract

### Safe to do autonomously
- Read any file; run `./venv/bin/python -m pytest`
- Edit files in `gui/`, `shared/`, `commands/`, `cli/`, `experimental/`
- Add or modify tests

### Confirm with the user first
- Modifying `requirements.txt` or `experimental/webui/requirements-web.txt`
- Changing DB schema (`tools/db_schema.sql`) or migration logic (`shared/db_migrations.py`)
- Touching auth code (`experimental/webui/auth.py`, `shared/smb_adapter.py`)
- Modifying CI/CD config (`.github/workflows/`)
- Any `git push`, force op, or branch deletion

### Hard no-nos
- Bypassing the GUI → CLI subprocess boundary (calling `UnifiedWorkflow` from GUI code)
- Using `gui/main.py` as a runtime entry point
- Using `tkinter.messagebox` directly in any GUI component
- Constructing `~/.dirracuda` paths by hand instead of calling `get_paths()`
- Writing tests that hit real Shodan, Censys, or live network hosts
