# Web UI Roadmap

Work one card at a time. Do not batch cards unless HI explicitly asks.

## Phase 0 - Contract Freeze

Goal: prove the current app state and lock the implementation boundary.

- Record branch/status.
- Record line counts for likely touched files.
- Confirm existing Experimental tab order.
- Confirm baseline tests and known failures.
- Confirm no web UI package exists yet.

## Phase 1 - Service Skeleton

Goal: start a local authenticated FastAPI service with no scan side effects.

- Add web-only dependencies in `webui/requirements-web.txt`.
- Add `webui/` package.
- Add config loader for `webui.json`.
- Add credential setup/verification.
- Add login/logout/session middleware.
- Add `/health`.
- Add minimal templates/static.

## Phase 2 - Scan Queue

Goal: queue and run existing CLI scans safely.

- Add validated scan request schema.
- Add task registry and one-active-scan worker.
- Launch CLI subprocesses with argument lists.
- Parse progress conservatively.
- Add cancel.
- Add tests for validation and subprocess command construction.

## Phase 3 - Read-Only Data Surfaces

Goal: show useful data without browser-side target file access.

- Add results summary endpoints/pages for SMB, FTP, HTTP.
- Include share/directory summary fields where existing DB/probe data supports
  them.
- Support post-scan probe status from web-launched scans.
- Add endpoint copy values.
- Add database export.
- Keep queries parameterized and legacy-shape tolerant.

## Phase 4 - Desktop Integration

Goal: add the Web UI control surface without redesigning Experimental.

- Add `webui_tab.py`.
- Insert tab after `Reddit` and before `Dorkbook`.
- Add `webui_control_dialog.py`.
- Wire dashboard context callback.
- Add focused registry/tab/control tests.

## Phase 5 - Remote and Packaging

Goal: support remote mode only when explicitly and safely configured.

- Enable TLS by default and require explicit override to disable it.
- Enforce allowlist for non-loopback bind.
- Add service command docs.
- Add systemd unit template if HI wants daemon management in v1.
- Add installer/control-dialog hooks only after manual review.

## Phase 6 - Closeout ← active (C9)

Goal: make the docs match reality and leave a clean handoff.

- Update root `README.md`.
- Update `docs/TECHNICAL_REFERENCE.md`.
- Update this planning workspace with final status and lessons.
- Run focused and wider validation.
- Leave HI manual gates explicit.
