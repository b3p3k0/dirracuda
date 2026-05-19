# C1 -- Web UI Dependency and Package Scaffold

## Issue

No `webui/` package exists yet. C1 creates the disabled-by-default scaffold so that future cards (C2-C9) have a clean, importable base to build on. Zero product behavior changes outside the new package and doc updates.

---

## Design reason

- Web-only dependencies (FastAPI, Uvicorn, Jinja2) must stay out of `requirements.txt` so the core tool remains installable without them. `webui/requirements-web.txt` keeps like items bundled and reduces root clutter (LESSONS_LEARNED Sec. 14; user preference).
- `fastapi>=0.112.0`: at 0.112.0, FastAPI stopped bundling standard optional deps in the plain package, which matches the "accepted dependencies only" intent of C1 (FastAPI release notes).
- Plain `uvicorn>=0.29.0` (no `[standard]` extra) -- transitive packages (uvloop, httptools, websockets) not yet approved for this card.
- `openapi_url=None, docs_url=None, redoc_url=None` on the FastAPI instance fully disables the schema endpoint and both docs UIs. Setting only `docs_url` and `redoc_url` leaves `/openapi.json` exposed (FastAPI metadata docs).
- Port 5480 is the canonical default per SPEC.md Sec. Config (line 110), SECURITY_MODEL.md Sec. Config example (line 91), and ASCII_SKETCHES.md (lines 42, 80, 144).
- `webui/__init__.py` carries only a module docstring -- no `WEBUI_ENABLED` sentinel. C7 gates desktop launch from config/state, not a package constant.
- FastAPI imports are scoped inside individual test functions so `test_webui_package_importable` does not require FastAPI at pytest collection time.
- Five planning docs still reference root-level `requirements-web.txt`; C1 updates all five to `webui/requirements-web.txt` to keep the docs consistent with the approved path decision.

---

## Proposed plan

### Step 1 -- `webui/requirements-web.txt` (new file)

```
# Web UI optional dependencies - install with: pip install -r webui/requirements-web.txt
fastapi>=0.112.0
uvicorn>=0.29.0
jinja2>=3.1.0
```

### Step 2 -- `webui/__init__.py` (new file)

```python
"""Web UI package - optional, disabled by default. Requires webui/requirements-web.txt."""
```

Docstring only. No FastAPI imports -- package remains importable without requirements-web.txt installed.

### Step 3 -- `webui/app.py` (new file)

`health()` defined at module level so tests can call it directly without httpx.

```python
from fastapi import FastAPI


def health() -> dict:
    return {"status": "ok"}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dirracuda Web UI",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.get("/health")(health)
    return app
```

### Step 4 -- `webui/server.py` (new file)

```python
import uvicorn
from webui.app import create_app


def run(host: str = "127.0.0.1", port: int = 5480) -> None:
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    run()
```

Supports `python -m webui.server`. Binds loopback-only at port 5480 by default.

### Step 5 -- `webui/tests/__init__.py` (new file)

Empty -- makes `webui/tests/` a package, consistent with project test conventions.

### Step 6 -- `webui/tests/test_scaffold.py` (new file)

Five tests, no HTTP client required. FastAPI imports scoped inside each test function.
`test_debug_endpoints_not_registered` inspects all route paths (not only APIRoute paths) so
`/openapi.json` is caught even when FastAPI registers it as a non-APIRoute helper route.

```python
"""C1 scaffold: import and app-factory tests."""


def test_webui_package_importable():
    import webui  # must not raise even without requirements-web.txt installed


def test_create_app_returns_fastapi_instance():
    from fastapi import FastAPI
    from webui.app import create_app

    assert isinstance(create_app(), FastAPI)


def test_health_route_registered():
    from fastapi.routing import APIRoute
    from webui.app import create_app

    app = create_app()
    paths = [r.path for r in app.routes if isinstance(r, APIRoute)]
    assert "/health" in paths


def test_health_route_payload():
    from webui.app import health

    assert health() == {"status": "ok"}


def test_debug_endpoints_not_registered():
    from webui.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    for disabled in ("/docs", "/redoc", "/openapi.json"):
        assert disabled not in paths, f"{disabled} must not be registered"
```

### Step 7 -- Update 5 planning docs (existing files, targeted edits)

Each edit changes only the `requirements-web.txt` reference to `webui/requirements-web.txt`.

| File | Line(s) | Change |
|------|---------|--------|
| `docs/dev/webui/README.md` | 13 | `requirements-web.txt` -> `webui/requirements-web.txt` |
| `docs/dev/webui/TASK_CARDS.md` | 52, 58 | same |
| `docs/dev/webui/CLAUDE_PROMPTS.md` | 32, 76 | same |
| `docs/dev/webui/ROADMAP.md` | 19 | same |
| `docs/dev/webui/LESSONS_LEARNED.md` | 28 | same |

### Step 8 -- `docs/dev/webui/approved_plans/C1_webui_dependency_and_package_scaffold.md` (this file)

Saved to the canonical location after RA/HI sign-off, before implementation begins.

---

## Files expected to change

| Action | Path |
|--------|------|
| CREATE | `webui/requirements-web.txt` |
| CREATE | `webui/__init__.py` |
| CREATE | `webui/app.py` |
| CREATE | `webui/server.py` |
| CREATE | `webui/tests/__init__.py` |
| CREATE | `webui/tests/test_scaffold.py` |
| CREATE | `docs/dev/webui/approved_plans/C1_webui_dependency_and_package_scaffold.md` |
| EDIT | `docs/dev/webui/README.md` |
| EDIT | `docs/dev/webui/TASK_CARDS.md` |
| EDIT | `docs/dev/webui/CLAUDE_PROMPTS.md` |
| EDIT | `docs/dev/webui/ROADMAP.md` |
| EDIT | `docs/dev/webui/LESSONS_LEARNED.md` |

**No product source files are modified.** `requirements.txt`, `./dirracuda`, `gui/main.py`, and all other source files are untouched.

---

## Validation planned

```bash
# 0. Install web deps
./venv/bin/python -m pip install -r webui/requirements-web.txt

# 0b. Check for dependency conflicts
./venv/bin/python -m pip check

# 1. Syntax check all three production modules
./venv/bin/python -m py_compile webui/__init__.py webui/app.py webui/server.py

# 2. Run C1 tests
./venv/bin/python -m pytest webui/tests -q

# 3. Confirm quick-lane pre-existing failure is unchanged
./venv/bin/python -m pytest gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success -q
```

Expected: 5 new tests pass, pre-existing failure still fails with the same error, no other regressions.

---

## Sources

- `docs/dev/webui/SPEC.md` -- port 5480 (line 110)
- `docs/dev/webui/SECURITY_MODEL.md` -- port 5480 (line 91), loopback-only default, no debug endpoints
- `docs/dev/webui/ASCII_SKETCHES.md` -- port 5480 (lines 42, 80, 144)
- `docs/dev/webui/ARCHITECTURE.md` -- package layout, entrypoint conventions
- `docs/dev/webui/LESSONS_LEARNED.md` Sec. 14 -- web deps in requirements-web.txt
- `docs/dev/webui/TASK_CARDS.md` C1 -- scope definition
- `docs/dev/webui/BASELINE_CONTRACTS.md` -- pre-existing failure reference, line-count guardrail
- FastAPI install: https://fastapi.tiangolo.com/tutorial/
- FastAPI OpenAPI/docs metadata: https://fastapi.tiangolo.com/tutorial/metadata/
- Uvicorn settings: https://www.uvicorn.org/settings/

---

## Risks / blockers

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `httpx` needed for route tests | None -- tests use `app.routes` introspection and direct function call | If any test requires `httpx`, stop and ask RA before adding it |
| FastAPI/uvicorn conflict with pinned `cryptography==46.0.7` | Low | `pip check` after install surfaces any conflict before proceeding |
| `webui/` shadows existing import | None -- confirmed no `webui/` in repo | Verified by directory listing |
| `./dirracuda` line count affected | None -- file not touched | Baseline 1700 lines; zero changes planned |
| Any new file exceeds 1700 lines | None -- all new files <=35 lines | Stop and propose modularization if limit approached |
| Planning doc edits touch product behavior | None -- docs-only edits, path string change only | Review each diff before committing |
