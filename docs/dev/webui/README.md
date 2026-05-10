# Dirracuda Web UI Planning Workspace

Status: C0–C8 complete and committed. C9 (docs and closeout) in progress.

This workspace turns the first-pass web UI notes in `INITIAL_PLANNING/` into a
Claude-ready plan. The web UI is optional, disabled by default, and scoped to
safe operator workflows first: scan launch, scan progress, read-only host
summaries, database export, and limited web UI configuration.

## Locked Decisions

- Framework: FastAPI + Uvicorn + Jinja2 are acceptable new dependencies.
  Web-only dependencies go in `webui/requirements-web.txt`; keep the main runtime
  requirements file focused on CLI/desktop dependencies.
- Scan execution: v1 uses existing CLI entrypoints through `subprocess` with
  `shell=False`, not direct `shared/*Workflow` calls. This matches the current
  desktop GUI boundary and keeps crashes/cancellation outside the web process.
- Auth: v1 uses server-side sessions with opaque random session IDs in
  `HttpOnly` cookies. Bearer API tokens are deferred.
- Password storage: v1 uses stdlib PBKDF2-HMAC-SHA256 with a unique salt and at
  least 600,000 iterations. Argon2id can be revisited later if HI approves an
  additional password-hashing dependency.
- Remote access: v1 supports remote use, but it is disabled by default. TLS is
  enabled/required by default, with an explicit operator opt-out. Non-loopback
  binding requires explicit config and an allowlist.
- Desktop integration: add a `Web UI` tab to the existing tabbed Experimental
  dialog. Insert it between `Reddit` and `Dorkbook`. The tab contains a short
  description and one button: `Open Web UI Control`.
- Existing desktop layout stays intact. No left-side menu or broader redesign of
  the Experimental dialog.
- Share/file browsing, in-browser downloads, DB import/merge, and experimental
  modules are out of v1 web scope.

## Files In This Workspace

- `SPEC.md` - v1 behavior, non-goals, acceptance criteria.
- `ARCHITECTURE.md` - process model, code layout, scan/task/data flow.
- `SECURITY_MODEL.md` - auth, remote exposure, input, subprocess, DB, and logs.
- `ASCII_SKETCHES.md` - desktop tab/control dialog and web page sketches.
- `ROADMAP.md` - card sequence and phase boundaries.
- `TASK_CARDS.md` - implementation cards for Claude.
- `CLAUDE_PROMPTS.md` - copy-paste prompts for DA execution.
- `VALIDATION_PLAN.md` - automated and HI manual gates.
- `RISK_REGISTER.md` - known risks and mitigations.
- `LESSONS_LEARNED.md` - carry-forward guardrails for future agents.
- `INITIAL_PLANNING/` - raw initial notes, preserved for traceability.

## Canonical Repo Context

- Runtime GUI entrypoint: `./dirracuda`.
- Legacy GUI shim: `gui/main.py` is import-compatible only.
- Existing GUI scan boundary: `gui/utils/backend_interface/interface.py` launches
  CLI subprocesses and parses output.
- Current experimental dialog: `gui/components/experimental_features_dialog.py`
  builds a `ttk.Notebook`; tab order comes from
  `gui/components/experimental_features/registry.py`.
- Test conventions: `./venv/bin/python -m pytest`, targeted GUI/shared tests,
  and `./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick`.
- GUI tests may need `xvfb-run -a`.

## Sources Checked

- Local repo: `README.md`, `CLAUDE.md`, `docs/TECHNICAL_REFERENCE.md`, and
  current Experimental dialog code.
- AI field/development/doc style guides:
  - https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_FIELD_GUIDE.md
  - https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/agent_sops/AI_AGENT_DEVELOPMENT_GUIDE.md
  - https://raw.githubusercontent.com/b3p3k0/configs/refs/heads/main/AI_AGENT_DOC_STYLE_GUIDE.md
- FastAPI docs:
  - https://fastapi.tiangolo.com/tutorial/background-tasks/
  - https://fastapi.tiangolo.com/tutorial/cors/
  - https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
- MDN responsive layout docs:
  - https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/CSS_layout/Media_queries
- OWASP cheat sheets:
  - https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
  - https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
  - https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- SQLite WAL docs: https://www.sqlite.org/wal.html
- Python subprocess docs: https://docs.python.org/3/library/subprocess.html
- systemd exec hardening docs:
  https://www.freedesktop.org/software/systemd/man/254/systemd.exec.html
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
