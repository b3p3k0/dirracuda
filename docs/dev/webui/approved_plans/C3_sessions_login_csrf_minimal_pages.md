# C3 -- Sessions, Login, CSRF, And Minimal Pages

Approved 2026-05-09. DA: Claude. RA: Codex. HI: Kevin.
Rev 2 approved after RA review resolving 10 findings.

## Context

C2 delivered secure config and credential storage. C3 wires authentication into the
FastAPI app: server-side sessions, login/logout endpoints, CSRF protection on mutating
routes, and a minimal protected dashboard page. Nothing in C3 launches scans, touches
results, or wires the desktop GUI (those are C4, C5, C7). The web UI remains
`enabled=False` by default.

## Issue

After C2, any request to a non-`/health` path either 404s or has no handler. C3
closes this gap so the remaining cards have a secure auth foundation to build on.

## Design Reason

- **Per-app SessionStore on `app.state`**: `create_app()` sets
  `app.state.session_store = SessionStore()`. No module-level singleton. Each
  `create_app()` call (including in tests) gets a fresh, isolated store.
- **Session ID entropy**: `secrets.token_hex(32)` -> 256 bits, well above OWASP
  64-bit minimum. CSRF token: same.
- **Synchronizer Token Pattern**: per-session CSRF token stored server-side in the
  Session record; embedded in dashboard template; validated on `POST /logout` via
  `hmac.compare_digest`.
- **Cookie naming**: `__Host-dirracuda-session` when TLS enabled (requires
  Secure+Path=/+no Domain); `dirracuda-session` otherwise. Flags: `HttpOnly`,
  `SameSite=Strict`, `Path=/`, `Secure` when TLS enabled.
- **JSON login/logout**: ARCHITECTURE.md line 180 -- "Login can submit JSON with a
  small script to avoid adding a form parser dependency in v1." No `python-multipart`.
  Login and logout use JSON body + tiny inline `<script>` in templates.
- **Origin/Referer check with `urllib.parse`**: normalized scheme+netloc comparison
  for Origin; netloc-only for Referer (intentional for localhost/dev where scheme
  may differ). Exact `netloc` equality prevents lookalike-domain attacks.
- **Origin/Referer on `/login`**: applied even before a session exists (OWASP notes
  Origin/Referer checking is valid for pre-session requests).
- **Logout without session**: returns 200 and clears cookie. No CSRF check (nothing
  to protect). Does not reveal whether a session existed.
- **Credential path injection**: `create_app(cfg, creds_path=None)` stores
  `app.state.creds_path`; login handler passes it to `verify_password()`. Tests
  supply a `tmp_path`-scoped credential file.
- **Thread safety**: `threading.Lock()` guards all `SessionStore` mutations.
  Safe for uvicorn single-worker asyncio default.
- **In-memory only**: sessions do not survive server restart; single-process,
  single-worker only in v1.

## CredentialError (C2 carry-forward)

`CredentialError` defined in `webui/auth.py` is not raised or caught in C2 or C3.
No action in C3 -- scope remains C3 only. Flagged for RA/HI decision: recommend
removing in C4 cleanup unless an external caller is planned.

## Files

| File | Action |
|------|--------|
| `webui/sessions.py` | new |
| `webui/dependencies.py` | new |
| `webui/templates/login.html` | new |
| `webui/templates/dashboard.html` | new |
| `webui/tests/test_sessions.py` | new |
| `webui/tests/test_csrf.py` | new |
| `webui/tests/test_login.py` | new |
| `webui/app.py` | modify |
| `webui/requirements-web.txt` | modify (add httpx>=0.27.0) |

Not modified: `webui/auth.py`, `webui/config.py`, `webui/server.py`,
`webui/__init__.py`, `requirements.txt`, all GUI files.

## Key Contracts

### webui/sessions.py

```python
COOKIE_NAME_SECURE = "__Host-dirracuda-session"
COOKIE_NAME_PLAIN  = "dirracuda-session"

@dataclass
class Session:
    username: str
    csrf_token: str
    created_at: float
    last_accessed: float

class SessionStore:
    def create(username) -> (session_id, csrf_token)
    def get(sid, idle, absolute) -> Optional[Session]  # enforces both timeouts
    def delete(sid) -> None

def cookie_name(tls_enabled: bool) -> str
```

### webui/dependencies.py

```python
class AuthRequired(Exception): ...

def get_session(request) -> Session    # raises AuthRequired on failure
def validate_csrf(request_token, session_csrf) -> bool  # hmac.compare_digest
def same_origin(request) -> bool       # urllib.parse, exact netloc match
```

### webui/app.py

```python
def create_app(cfg=None, creds_path=None) -> FastAPI:
    # app.state.cfg, app.state.session_store, app.state.creds_path
    # exception_handler(AuthRequired) -> RedirectResponse("/login", 303)
```

Routes:

| Method | Path | Auth | CSRF | Response |
|--------|------|------|------|----------|
| GET | `/health` | no | (none) | 200 JSON |
| GET | `/` | no | (none) | 303 -> /dashboard |
| GET | `/login` | no | (none) | 200 HTML |
| POST | `/login` | no | Origin/Referer | 200 JSON + Set-Cookie, or 401/403 |
| POST | `/logout` | no* | Origin+token (if session) | 200 JSON + delete cookie, or 403 |
| GET | `/dashboard` | Depends(get_session) | (none) | 200 HTML |

*Logout checks CSRF/origin only when a valid session is found.

## Validation

```bash
./venv/bin/python -m pip install -r webui/requirements-web.txt
./venv/bin/python -m py_compile webui/app.py webui/auth.py webui/config.py \
  webui/sessions.py webui/dependencies.py
./venv/bin/python -m pytest \
  webui/tests/test_sessions.py webui/tests/test_csrf.py webui/tests/test_login.py \
  -v
./venv/bin/python -m pytest webui/tests/ -q
./venv/bin/python -m pip check
./venv/bin/python -m pytest \
  gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success \
  -q
```

Pre-existing failure `test_s10_se_dork_probe_task_lifecycle_success` must remain
unchanged.

## Sources

- OWASP Session Management Cheat Sheet (session ID entropy, cookie flags, `__Host-`
  prefix, idle/absolute timeout bounds, synchronizer token pattern):
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- OWASP CSRF Prevention Cheat Sheet (synchronizer token, Origin/Referer including
  pre-session use, exact netloc matching requirement):
  https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- FastAPI TestClient requires httpx:
  https://fastapi.tiangolo.com/tutorial/testing/
- FastAPI forms require python-multipart (confirming we avoid this via JSON):
  https://fastapi.tiangolo.com/tutorial/request-form-models/
- `docs/dev/webui/ARCHITECTURE.md` line 180: JSON login avoids form parser dependency
- `docs/dev/webui/SECURITY_MODEL.md`: cookie name rules, timeout defaults/bounds,
  CSRF spec, logging rules
- `webui/config.py`: `WebUIConfig` defaults
  (`session_timeout_idle=1800`, `session_timeout_absolute=28800`, `tls.enabled=True`)
- `webui/auth.py`: `verify_password(username, password, path=None)` signature
