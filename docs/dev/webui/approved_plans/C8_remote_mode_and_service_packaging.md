# C8 — Remote Mode and Service Packaging

## Context

C1–C7 built the web UI scaffold, auth/sessions, scan queue, results, frontend, and desktop tab. The server currently starts with hardcoded defaults — it never loads config, never validates remote safety, and never enforces the IP allowlist at request time. `webui/config.py` already has full validation logic (loopback rules, allowlist, TLS/insecure-override checks), but that validation only runs when the config file is read or saved — not at server startup, and not on each request. C8 closes both gaps.

## What's already done (do not re-implement)

- `webui/config.py::validate()` — all remote-mode rules are already encoded and tested in `test_config.py`. Do not duplicate.
- `webui/config.py::load_config()` — loads, parses, and calls `validate()`. Use it.
- `webui/config.py::_is_loopback()` — private; inline as `ipaddress.ip_address(addr).is_loopback` in server.py.

## Files to change

| File | Change |
|------|--------|
| `webui/server.py` | Load config, validate overrides, cert/key check for remote TLS, emit startup messages |
| `webui/app.py` | Add `_is_ip_allowed()` helper and `@app.middleware("http")` allowlist check (remote_enabled only) |
| `webui/tests/test_remote_mode.py` | New file — startup + allowlist tests |
| `README.md` | Expand Web UI section with remote mode setup and warnings |
| `docs/TECHNICAL_REFERENCE.md` | Add remote mode config, startup behavior, allowlist |
| `docs/dev/webui/LESSONS_LEARNED.md` | Append C8 lessons |

---

## 1. `webui/server.py` (17 → ~75 lines)

### Key design decisions (from review feedback)

**TLS gap fix**: For non-loopback bind with `tls.enabled=True`, cert_file and key_file must be non-empty and exist. A remote startup that has TLS "enabled" in config but no actual cert/key must fail — no silent HTTP downgrade. Extract this into a `_check_remote_tls(cfg, bind)` helper returning an error string or `None` (testable without mocking uvicorn).

**config_path propagation**: Pass `config_path` through to `create_app()` so the `/config` save endpoint writes to the file the server actually loaded, not the default path.

```python
import argparse
import ipaddress
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import uvicorn
from webui.app import create_app
from webui.config import WebUIConfig, WebUIConfigError, load_config, validate

logger = logging.getLogger(__name__)


def _check_remote_tls(cfg: WebUIConfig, bind: str) -> Optional[str]:
    """Return an error string if remote TLS is misconfigured, else None."""
    try:
        is_loopback = ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return f"invalid bind address: {bind!r}"
    if is_loopback:
        return None  # Localhost: no remote TLS requirement
    if cfg.tls.enabled:
        if not cfg.tls.cert_file or not cfg.tls.key_file:
            return "remote TLS requires tls.cert_file and tls.key_file to be set"
        if not Path(cfg.tls.cert_file).is_file():
            return f"TLS cert file not found: {cfg.tls.cert_file}"
        if not Path(cfg.tls.key_file).is_file():
            return f"TLS key file not found: {cfg.tls.key_file}"
    # TLS disabled for remote: allow_insecure_remote already checked by validate()
    return None


def _startup_lines(cfg: WebUIConfig, host: str, port: int) -> list:
    has_tls = cfg.tls.enabled and cfg.tls.cert_file and cfg.tls.key_file
    scheme = "https" if has_tls else "http"
    mode = "remote" if cfg.remote_enabled else "localhost"
    lines = [f"Web UI starting: mode={mode}  url={scheme}://{host}:{port}"]
    if cfg.remote_enabled:
        lines.append(f"  allowlist: {cfg.allowed_cidrs}")
    if not cfg.tls.enabled:
        msg = "WARNING: TLS disabled for remote mode" if cfg.remote_enabled else "TLS disabled (localhost only)"
        lines.append(f"  {msg}")
    elif not has_tls:
        lines.append("  NOTE: TLS cert/key not configured — serving HTTP (localhost only)")
    return lines


def run(
    host: Optional[str] = None,
    port: Optional[int] = None,
    cfg: Optional[WebUIConfig] = None,
    config_path=None,
) -> None:
    if cfg is None:
        try:
            cfg = load_config(config_path)
        except WebUIConfigError as exc:
            logger.error("Web UI config error: %s", exc)
            sys.exit(1)

    bind = host if host is not None else cfg.bind_address
    bind_port = port if port is not None else cfg.port

    if host is not None or port is not None:
        try:
            overridden = replace(cfg, bind_address=bind, port=bind_port)
            validate(overridden)
            cfg = overridden
        except WebUIConfigError as exc:
            logger.error("Web UI startup refused: %s", exc)
            sys.exit(1)

    err = _check_remote_tls(cfg, bind)
    if err:
        logger.error("Web UI startup refused: %s", err)
        sys.exit(1)

    for line in _startup_lines(cfg, bind, bind_port):
        logger.info("%s", line)

    ssl_certfile = cfg.tls.cert_file or None
    ssl_keyfile = cfg.tls.key_file or None
    if cfg.tls.enabled and ssl_certfile and ssl_keyfile:
        uvicorn.run(create_app(cfg=cfg, config_path=config_path), host=bind, port=bind_port,
                    ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)
    else:
        uvicorn.run(create_app(cfg=cfg, config_path=config_path), host=bind, port=bind_port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, dest="config_path")
    args = parser.parse_args()
    run(host=args.host, port=args.port, config_path=args.config_path)
```

`service_control.py` is unchanged — still passes `--host`/`--port` as subprocess args, which are now treated as validated overrides.

---

## 2. `webui/app.py` (~15 new lines in ~368)

### Key design decision (from review feedback)

**Allowlist guard**: Enforce only when `cfg.remote_enabled=True`. All existing tests use `remote_enabled=False` (the default) and default TestClient host `"testclient"` — they must not be affected. The `_networks` list is still built always (used for when remote_enabled is toggled), but the per-request check is gated.

Add at module top: `import ipaddress`

Add before `create_app()`:
```python
def _is_ip_allowed(host, networks) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks)
```

Inside `create_app()`, after `app = FastAPI(...)`:
```python
_networks = [ipaddress.ip_network(c, strict=False) for c in cfg.allowed_cidrs]

@app.middleware("http")
async def _allowlist_check(request: Request, call_next):
    if not cfg.remote_enabled:
        return await call_next(request)
    host = request.client.host if request.client else None
    if not _is_ip_allowed(host, _networks):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await call_next(request)
```

`Optional`, `JSONResponse`, and `Request` are already imported. No new imports needed beyond `ipaddress`.

---

## 3. `webui/tests/test_remote_mode.py` (new, ~110 lines)

### Fixtures / helpers

**`_with_client_ip(app, ip)`**: ASGI scope wrapper that overrides `scope["client"]` so the middleware sees a controllable IP. Standard pattern for ASGI IP middleware testing.

```python
def _with_client_ip(app, client_ip: str):
    async def _wrapper(scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(client_ip, 12345))
        await app(scope, receive, send)
    return _wrapper
```

**`_remote_app(allowed_cidrs)`**: returns `create_app(cfg=...)` with a valid remote config using `TLSConfig(enabled=False, allow_insecure_remote=True)` to avoid needing real certs in tests.

### Tests (9)

| Test | Method | Assertion |
|------|--------|-----------|
| `test_localhost_startup_allowed` | call `validate(WebUIConfig())` | does not raise |
| `test_nonloopback_remote_disabled_rejected` | call `validate(...)` | raises `WebUIConfigError` matching `remote_enabled` |
| `test_nonloopback_empty_cidrs_rejected` | call `validate(...)` | raises matching `allowed_cidrs` |
| `test_nonloopback_tls_off_no_override_rejected` | call `validate(...)` | raises matching `allow_insecure_remote` |
| `test_explicit_insecure_override_allowed` | call `validate(...)` | does not raise |
| `test_remote_tls_no_cert_rejected` | call `_check_remote_tls(cfg, "0.0.0.0")` | returns non-None error string |
| `test_allowlist_blocks_disallowed_client` | `GET /health` via `_with_client_ip(app, "192.168.1.5")`, allowlist `10.0.0.0/8` | 403 |
| `test_allowlist_permits_allowed_client` | `GET /health` via `_with_client_ip(app, "10.0.1.5")`, allowlist `10.0.0.0/8` | 200 |
| `test_localhost_mode_skips_allowlist` | default config (`remote_enabled=False`), `_with_client_ip(app, "203.0.113.1")` | 200 (no allowlist check) |

Test 6 uses `_check_remote_tls()` directly — no uvicorn mocking needed. Tests 7–9 use `TestClient(wrapped, follow_redirects=False)`.

---

## 4. `README.md` (expand Web UI section ~+20 lines)

Add under `## Web UI (Optional)`:
- Default mode: localhost only (`127.0.0.1:5480`), no TLS cert required for local use
- Remote mode prerequisites: set `remote_enabled=true`, configure `allowed_cidrs`, provide TLS cert/key **or** set `tls.allow_insecure_remote=true` (explicit opt-out, not recommended)
- Config file location: `~/.dirracuda/conf/webui.json`
- Warning callout: non-loopback bind without proper TLS or `allow_insecure_remote` is rejected at startup

---

## 5. `docs/TECHNICAL_REFERENCE.md` (~+25 lines)

Add a subsection under the Web UI block:
- Config fields: `bind_address`, `remote_enabled`, `allowed_cidrs`, `tls.{enabled,cert_file,key_file,allow_insecure_remote}`
- Startup enforcement: 4 bullet rules (loopback-always-ok; non-loopback requires remote_enabled, non-empty cidrs, TLS-or-insecure-override, cert+key if TLS on)
- Allowlist middleware: "applied at request time only when `remote_enabled=True`; localhost mode skips check entirely"

---

## 6. `docs/dev/webui/LESSONS_LEARNED.md` (append C8 section ~15 lines)

**`## C8 — Remote Mode`** with 4 lessons:
1. Validate at server startup via `load_config()`, not just at config save time.
2. Allowlist middleware must be gated on `remote_enabled=True`; unconditional allowlist breaks TestClient fixtures (`"testclient"` host is not an IP).
3. `_check_remote_tls()` helper makes startup TLS validation testable without mocking uvicorn — extract validations that need testing from side-effecting code.
4. Propagate `config_path` through `run()` → `create_app()` so the `/config` save endpoint writes to the file the server loaded.

---

## Validation

```bash
# Syntax check
./venv/bin/python -m py_compile webui/server.py webui/app.py webui/config.py

# New tests
./venv/bin/python -m pytest webui/tests/test_remote_mode.py -q

# Regression — existing webui tests must pass unchanged
./venv/bin/python -m pytest webui/tests/test_login.py webui/tests/test_scan_routes.py webui/tests/test_results.py webui/tests/test_export.py -q

# Full webui suite
./venv/bin/python -m pytest webui/tests/ -q
```

## HI test needed

- Start server in localhost mode, confirm startup log shows `mode=localhost` and correct URL.
- Attempt non-loopback start without remote_enabled — confirm startup refuses.
- Confirm disallowed-IP test requires remote config + allowlist entry.

## Rubric — touched file line counts

| File | Before | Expected after |
|------|--------|---------------|
| `webui/server.py` | 17 | ~75 |
| `webui/app.py` | 368 | ~392 |
| `webui/tests/test_remote_mode.py` | 0 | ~110 |
| `README.md` | ~510 | ~532 |
| `docs/TECHNICAL_REFERENCE.md` | ~1200 | ~1226 |
| `docs/dev/webui/LESSONS_LEARNED.md` | 61 | ~78 |
