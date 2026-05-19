# C2 -- Web UI Config And Credential Store

Approved 2026-05-09. DA: Claude. RA: Codex. HI: Kevin.

## Context

C1 delivered the minimum `webui/` scaffold: package init, app factory, `/health`, and
tests. C2 lays the security foundation that C3 (sessions), C4 (scan launch), and C8
(remote mode) depend on. Nothing in C2 is wired into the live FastAPI app. C2 is
stdlib-only; no new pip dependencies. TLS cert/key path readability is explicitly C8
scope -- C2 validates only the structural remote-safety rules.

## Issue

The service needs secure local config and credentials before any protected UI can
exist.

## Design Reason

- **Separate modules**: `config.py` owns policy enforcement; `auth.py` owns crypto.
- **Strict parsing, no coercion**: Unknown keys rejected. Wrong types rejected. `bool`
  rejected for integer fields (`isinstance(v, bool)` check before `isinstance(v, int)`
  since `bool` is an `int` subclass in Python).
- **Fail-closed validation**: Remote-mode rules enforced at load time.
- **PBKDF2-HMAC-SHA256 at 600,000 iterations** per OWASP 2022. Salt: 32 bytes from
  `os.urandom`. `hmac.compare_digest` for constant-time comparison. Password capped
  at 1,024 bytes *after UTF-8 encoding*, before PBKDF2.
- **Explicit credential schema**: Stored record includes `algorithm` field for
  forward compatibility; `verify_password` returns `False` for unknown algorithm,
  malformed hex, bad iteration type, or iteration below policy minimum.
- **Atomic writes with fsync**: `mkstemp(dir=path.parent)` -> write -> `flush()` ->
  `os.fsync(fd)` -> `chmod(0o600)` -> `os.replace()` -> best-effort directory fsync
  on POSIX. Temp cleaned on any exception.
- **Username validation**: Non-empty, no leading/trailing whitespace or control chars,
  capped at 128 bytes.
- **No new deps**: All stdlib.

## Files

| File | Action |
|------|--------|
| `webui/config.py` | new |
| `webui/auth.py` | new |
| `webui/tests/test_config.py` | new |
| `webui/tests/test_auth.py` | new |

No existing files modified. `webui/app.py` untouched.

## Key Contracts

### webui/config.py

Structures (dataclasses with factories):

```python
@dataclass
class TLSConfig:
    enabled: bool = True
    cert_file: str = ""
    key_file: str = ""
    allow_insecure_remote: bool = False

@dataclass
class WebUIConfig:
    enabled: bool = False
    bind_address: str = "127.0.0.1"
    port: int = 5480
    remote_enabled: bool = False
    allowed_cidrs: list = field(default_factory=lambda: ["127.0.0.1/32", "::1/128"])
    session_timeout_idle: int = 1800
    session_timeout_absolute: int = 28800
    tls: TLSConfig = field(default_factory=TLSConfig)
```

`_parse_config(raw)` -- strict: rejects unknown keys, wrong types, bool-for-int.

`validate(cfg)` -- enforces port range, session bounds, CIDR parseability, and all
remote-mode rules. Raises `WebUIConfigError` on any failure.

Remote-mode rules (non-loopback only):
1. `remote_enabled` must be `True`
2. `allowed_cidrs` must be non-empty
3. `tls.enabled` False requires `tls.allow_insecure_remote` True

`_atomic_write_json(data, path)` -- defined here, imported by `auth.py`.

`load_config(path=None)` -- absent file returns defaults; validates on load.

`save_config(cfg, path=None)` -- validates then writes atomically.

### webui/auth.py

```python
PBKDF2_ITERATIONS = 600_000
PBKDF2_ALGORITHM = "pbkdf2_hmac_sha256"
SALT_BYTES = 32
MAX_PASSWORD_BYTES = 1024
MAX_USERNAME_BYTES = 128
```

Credential record schema:
```json
{
  "algorithm": "pbkdf2_hmac_sha256",
  "iterations": 600000,
  "salt": "<hex>",
  "hash": "<hex>"
}
```

`set_password(username, password, path=None)` -- raises `ValueError` for invalid
username or overlong password.

`verify_password(username, password, path=None)` -- returns `False` (never raises)
for any failure: missing file, missing user, unknown algorithm, malformed hex, bool
iterations, iterations below policy, overlong password.

`credential_exists(path=None)` -- bool.

## Validation

```bash
./venv/bin/python -m py_compile webui/config.py webui/auth.py
./venv/bin/python -m pytest webui/tests/test_config.py webui/tests/test_auth.py -q
./venv/bin/python -m pip check
./venv/bin/python -m pytest webui/tests/ -q
```

Pre-existing failure `test_s10_se_dork_probe_task_lifecycle_success` must remain
unchanged.

## Sources

- OWASP Password Storage Cheat Sheet (PBKDF2-HMAC-SHA256, 600k iterations, 2022):
  https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- Python `hashlib.pbkdf2_hmac`: https://docs.python.org/3/library/hashlib.html
- Python `ipaddress`: https://docs.python.org/3/library/ipaddress.html
- Python `tempfile` + `os.replace`: https://docs.python.org/3/library/tempfile.html
  and https://docs.python.org/3/library/os.html
