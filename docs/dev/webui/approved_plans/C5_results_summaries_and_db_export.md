# C5 — Results Summaries and Database Export

## Context

C1-C4 delivered the webui package (app factory, auth/sessions/CSRF, scan queue, CLI subprocess runner). C5 adds read-only protocol result pages and a controlled DB export, completing the first useful operator view of discovered data. No product code exists yet for this card — `webui/db.py`, the results routes, the results template, and the two test files are all new.

Issue: Web UI C4 has scan launch/queue but no usable read-only protocol results or DB export surface for v1.

## Hard Constraints (carry from task card)

- No new dependencies. FastAPI, Uvicorn, Jinja2, httpx already approved; that's all.
- Parameterized SQL only. No string-interpolated query values.
- Runtime schema guards (`sqlite_master` + `PRAGMA table_info`) for all optional tables/columns.
- Explicit bounds validation on pagination/filter inputs.
- Export: controlled dir (`~/.dirracuda/exports/`), generated timestamp filename; no user input to filename.
- Do NOT expose browser file explorer or target file downloads (only the DB export artifact).
- No touched file may exceed 1700 lines.
- Do NOT commit.

## Files

### Created
- `webui/db.py` (~165 lines — extra column guards for FTP/HTTP, suffix in export_db, regex constant)
- `webui/templates/results.html` (~75 lines)
- `webui/tests/test_results.py` (~210 lines — added FTP/HTTP positive and missing-column cases)
- `webui/tests/test_export.py` (~150 lines — added allowlist, collision, and path-exposure tests)

### Modified
- `webui/app.py` (197 → ~270 lines, +73 lines — allowlist regex layer in download route)

All well within 1700-line limit. No modularization needed.

## Step 1 — webui/db.py (new file)

Pure-function module. No module-level state, no singleton connection.

### Constants

```python
_DEFAULT_DB_PATH = Path.home() / ".dirracuda" / "data" / "dirracuda.db"
_EXPORT_DIR = Path.home() / ".dirracuda" / "exports"
_PAGE_MIN, _PAGE_MAX = 1, 10000
_PAGE_SIZE_MIN, _PAGE_SIZE_MAX = 1, 200
_PAGE_SIZE_DEFAULT = 50
_COUNTRY_RE = re.compile(r'^[A-Z]{2}$')
```

### `_validate_bounds(page, page_size, country) -> (int, int, Optional[str])`

- page: int, 1–10000; raises ValueError outside range
- page_size: int, 1–200; raises ValueError outside range
- country: str or None; if present, strip+upper, must match `^[A-Z]{2}$`; return None if empty after strip

### `_inspect_tables(conn) -> set[str]`

```sql
SELECT name FROM sqlite_master WHERE type='table'
```

### `_inspect_columns(conn, table_name: str) -> set[str]`

```sql
PRAGMA table_info(<table_name>)   -- table_name is a literal string from our code, never user input
```
Returns set of column names (index 1 in each row).

### `_connect(db_path: Path) -> sqlite3.Connection`

```python
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
```
Read-only URI (`mode=ro`). Raises `OperationalError` if DB absent or locked. Callers are responsible for closing — all three reader functions use try/finally:

```python
conn = _connect(db_path)
try:
    # ... queries ...
    return result
finally:
    conn.close()
```

### `get_smb_results(db_path, page, page_size, country) -> list[dict]`

Guards:
1. Return `[]` if `not db_path.exists()`
2. Return `[]` if `"smb_servers" not in _inspect_tables(conn)`
3. Check `"share_access" in tables` → `has_sa`
4. If `has_sa`: check `"share_comment" in _inspect_columns(conn, "share_access")` → `has_comment`

SQL when `has_sa=True`:
```sql
SELECT
    s.ip_address, s.country, s.country_code, s.auth_method, s.status,
    s.first_seen, s.last_seen, s.scan_count,
    COUNT(CASE WHEN sa.accessible THEN 1 END) AS accessible_count,
    GROUP_CONCAT(CASE WHEN sa.accessible THEN sa.share_name END) AS accessible_names,
    <comment_expr> AS share_comments
FROM smb_servers s
LEFT JOIN share_access sa ON s.id = sa.server_id
[WHERE s.country_code = ?]
GROUP BY s.id
ORDER BY s.last_seen DESC, s.id DESC
LIMIT ? OFFSET ?
```
`<comment_expr>` = `GROUP_CONCAT(CASE WHEN sa.accessible THEN sa.share_comment END)` if `has_comment` else `NULL`

SQL when `has_sa=False`:
```sql
SELECT ..., 0 AS accessible_count, NULL AS accessible_names, NULL AS share_comments
FROM smb_servers s
[WHERE s.country_code = ?]
ORDER BY s.last_seen DESC, s.id DESC
LIMIT ? OFFSET ?
```

Output row keys: `ip`, `country`, `country_code`, `auth_method`, `status`, `first_seen`, `last_seen`, `scan_count`, `accessible_shares` (int), `share_names` (list, split on `,`), `copy_str` = `f"smb://{ip}"`

### `get_ftp_results(db_path, page, page_size, country) -> list[dict]`

Guards:
1. Return `[]` if DB absent
2. Return `[]` if `"ftp_servers" not in tables`
3. Check `"ftp_probe_cache" in tables` → `has_probe`
4. **If `has_probe`: inspect columns → `has_dirs_count = "accessible_dirs_count" in _inspect_columns(conn, "ftp_probe_cache")`**
   - If column absent, treat probe table as if not present (use `0 AS accessible_dirs`)

SQL when `has_probe=True` AND `has_dirs_count=True`:
```sql
SELECT ...,
    COALESCE(fpc.accessible_dirs_count, 0) AS accessible_dirs
FROM ftp_servers s
LEFT JOIN ftp_probe_cache fpc ON s.id = fpc.server_id
[WHERE s.country_code = ?]
ORDER BY s.last_seen DESC, s.id DESC LIMIT ? OFFSET ?
```

SQL when `has_probe=False` OR `has_dirs_count=False`:
```sql
SELECT ..., 0 AS accessible_dirs
FROM ftp_servers s
[WHERE s.country_code = ?]
ORDER BY s.last_seen DESC, s.id DESC LIMIT ? OFFSET ?
```

Output row keys: `ip`, `country`, `country_code`, `port`, `anon_accessible` (bool), `status`, `first_seen`, `last_seen`, `scan_count`, `accessible_dirs` (int), `copy_str` = `f"ftp://{ip}:{port}"`

### `get_http_results(db_path, page, page_size, country) -> list[dict]`

Guards:
1. Return `[]` if DB absent
2. Return `[]` if `"http_servers" not in tables`
3. Check `"http_access" in tables` → `has_ha`
4. **If `has_ha`: inspect columns → `ha_cols = _inspect_columns(conn, "http_access")`**
   - `has_dir_count = "dir_count" in ha_cols`
   - `has_file_count = "file_count" in ha_cols`
   - `has_is_index = "is_index_page" in ha_cols`
   - `has_accessible = "accessible" in ha_cols`
   - Per-column: if missing, substitute `0` literal in SELECT rather than referencing the column

SQL shape when `has_ha=True` (each optional column guarded individually):
```sql
SELECT
    s.ip_address, s.country, s.country_code, s.port, s.scheme, s.title,
    s.status, s.first_seen, s.last_seen, s.scan_count,
    <dir_count_expr>      AS dir_count,
    <file_count_expr>     AS file_count,
    <is_index_expr>       AS is_index_page,
    <accessible_expr>     AS last_accessible
FROM http_servers s
LEFT JOIN http_access ha ON s.id = ha.server_id
[WHERE s.country_code = ?]
GROUP BY s.id
ORDER BY s.last_seen DESC, s.id DESC LIMIT ? OFFSET ?
```

Where each `<expr>` is:
- `COALESCE(MAX(ha.dir_count), 0)` if `has_dir_count` else `0`
- `COALESCE(MAX(ha.file_count), 0)` if `has_file_count` else `0`
- `MAX(CASE WHEN ha.is_index_page THEN 1 ELSE 0 END)` if `has_is_index` else `0`
- `MAX(CASE WHEN ha.accessible THEN 1 ELSE 0 END)` if `has_accessible` else `0`

SQL when `has_ha=False`:
```sql
SELECT ..., 0 AS dir_count, 0 AS file_count, 0 AS is_index_page, 0 AS last_accessible
FROM http_servers s [WHERE ...] ORDER BY s.last_seen DESC, s.id DESC LIMIT ? OFFSET ?
```

Output row keys: `ip`, `country`, `country_code`, `port`, `scheme`, `title`, `status`, `first_seen`, `last_seen`, `scan_count`, `dir_count`, `file_count`, `is_index_page` (bool), `last_accessible` (bool), `copy_str` = `f"{scheme}://{ip}:{port}"`

### `export_db(db_path: Path, export_dir: Path) -> Path`

Uses `VACUUM INTO` — the same mechanism as `gui/utils/db_tools_engine_maintenance_methods.py::export_database()`. This produces a clean, defragmented copy and is the established product "Export" contract (README.md: "Export runs VACUUM INTO").

`backup()` is NOT used here (that is `quick_backup`, a separate operation).

**No-create source connection:** SQLite's default `connect(path)` uses `mode=rwc` which creates a new empty file if `path` does not exist. `VACUUM INTO` would then succeed against that empty file — silently exporting nothing. Fix: open with `mode=rw` (`?mode=rw`, `uri=True`), which is read-write but refuses to create if absent, raising `OperationalError` instead.

```python
export_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
suffix = secrets.token_hex(4)   # 8 hex chars — collision-safe under rapid calls
filename = f"dirracuda_export_{timestamp}_{suffix}.db"
dest_path = export_dir / filename
# retry once on the rare collision (clock skew + same suffix)
if dest_path.exists():
    suffix = secrets.token_hex(4)
    filename = f"dirracuda_export_{timestamp}_{suffix}.db"
    dest_path = export_dir / filename

# mode=rw: no-create — raises OperationalError if db_path absent (not silently empty)
conn = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
try:
    conn.execute("VACUUM INTO ?", (str(dest_path),))   # parameterized — dest is not user input
finally:
    conn.close()
return dest_path
```

Raises `OperationalError` if source DB is absent or locked; propagates to the route handler which returns 500. The `dest_path` string is purely generated — no user input touches it.

**Export filename pattern (used in download allowlist):**

```python
_EXPORT_FILENAME_RE = re.compile(r'^dirracuda_export_\d{8}_\d{6}_[0-9a-f]{8}\.db$')
```

---

## Step 2 — webui/app.py (modify)

### Import changes

```python
# Add to existing typing import:
from typing import Literal, Optional

# Add to existing fastapi import:
from fastapi import Depends, FastAPI, Query, Request

# Add to existing fastapi.responses import:
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

# Add new module-level import:
import webui.db as _db
```

Use module reference (`import webui.db as _db`) so `monkeypatch.setattr(_db, "_EXPORT_DIR", ...)` works in tests.

### `create_app()` signature

```python
def create_app(
    cfg: Optional[WebUIConfig] = None,
    creds_path=None,
    db_path=None,          # NEW: Path or None
) -> FastAPI:
```

Inside, after `app.state.scan_queue = ScanQueue()`:

```python
app.state.db_path = Path(db_path) if db_path is not None else _db._DEFAULT_DB_PATH
```

### New routes (add inside `create_app`)

**GET /results** — HTML, auth required
```python
@app.get("/results", response_class=HTMLResponse)
async def _results_page(request, session=Depends(get_session)):
    return templates.TemplateResponse(request, "results.html", {"session": session})
```

**GET /api/results/{protocol}** — JSON, auth required, bounded query params
```python
@app.get("/api/results/{protocol}")
async def _get_results(
    protocol: Literal["smb", "ftp", "http"],
    request: Request,
    session: Session = Depends(get_session),
    page: int = Query(default=1),
    page_size: int = Query(default=_db._PAGE_SIZE_DEFAULT),
    country: Optional[str] = Query(default=None),
) -> JSONResponse:
    try:
        p, ps, c = _db._validate_bounds(page, page_size, country)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    db_path = request.app.state.db_path
    readers = {
        "smb": _db.get_smb_results,
        "ftp": _db.get_ftp_results,
        "http": _db.get_http_results,
    }
    try:
        rows = readers[protocol](db_path, p, ps, c)
    except Exception:
        logger.exception("results query failed: protocol=%s", protocol)
        return JSONResponse({"error": "database error"}, status_code=500)
    return JSONResponse({"results": rows, "page": p, "page_size": ps})
```

**POST /api/export** — JSON, auth + CSRF required
```python
@app.post("/api/export")
async def _trigger_export(request: Request, session: Session = Depends(get_session)):
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
    db_path = request.app.state.db_path
    try:
        artifact = _db.export_db(db_path, _db._EXPORT_DIR)
    except Exception:
        logger.exception("export failed")
        return JSONResponse({"error": "export failed"}, status_code=500)
    logger.info("export created: filename=%s", artifact.name)
    return JSONResponse({"filename": artifact.name})   # never expose full path
```

**GET /api/export/{filename}** — FileResponse, auth required

Two-layer validation: (1) allowlist regex, (2) directory containment.

```python
@app.get("/api/export/{filename}")
async def _download_export(
    filename: str,
    request: Request,
    session: Session = Depends(get_session),
):
    # Layer 1: allowlist — only files matching the generated pattern are served
    if not _db._EXPORT_FILENAME_RE.fullmatch(filename):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    # Layer 2: directory containment (guards against symlinks / unexpected state)
    export_dir = _db._EXPORT_DIR
    if not export_dir.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    resolved_dir = export_dir.resolve()
    target = (export_dir / filename).resolve()
    try:
        target.relative_to(resolved_dir)
    except ValueError:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path=target, filename=filename, media_type="application/octet-stream")
```

---

## Step 3 — webui/templates/results.html (new file)

Minimal, consistent with `dashboard.html` and `scans.html`. Includes:
- `<meta name="csrf-token">` with CSRF token
- Protocol tab buttons (SMB / FTP / HTTP)
- Country filter input (maxlength=2) + Load/Prev/Next buttons
- `<table id="results-table">` populated by JS
- Export button that POSTs `/api/export` with CSRF, then opens `/api/export/{filename}` for download
- `<span role="status">` for error/success messages

---

## Step 4 — webui/tests/test_results.py (new file)

### Key fixtures

```python
@pytest.fixture
def db_smb_only(tmp_path):
    # real sqlite file: smb_servers with 2 rows (10.0.0.1/US, 10.0.0.2/DE), no share_access

@pytest.fixture
def db_with_shares(tmp_path):
    # smb_servers + share_access (with share_comment column)
    # Server 1: 1 accessible share "Public", 1 inaccessible "Private"

@pytest.fixture
def db_ftp_with_probe(tmp_path):
    # ftp_servers (1 row, port=21) + ftp_probe_cache (accessible_dirs_count=3)

@pytest.fixture
def db_ftp_probe_no_col(tmp_path):
    # ftp_probe_cache table WITHOUT accessible_dirs_count column

@pytest.fixture
def db_http_with_access(tmp_path):
    # http_servers (1 row, port=8080) + http_access (all summary columns present)

@pytest.fixture
def db_http_access_partial(tmp_path):
    # http_access with only id + server_id (no dir_count, file_count, etc.)

@pytest.fixture
def client(creds, cfg_no_tls, db_smb_only):
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=db_smb_only)
    return TestClient(app, follow_redirects=False)
```

### Test cases

**Auth protection:** 303→/login for unauthenticated page and API requests

**Pagination bounds:** page=0 → 400; page=10001 → 400; page_size=0 → 400; page_size=201 → 400; valid → 200

**Country filter:** `1A` → 400; `USA` → 400; `US` → 200 filtered; `us` → 200 normalized to `US`

**Invalid protocol:** `rdp` → 422 (FastAPI Literal validation)

**SMB content:** 2 rows returned; `copy_str` starts with `smb://`; `accessible_shares==0` when no share_access

**Share summaries:** accessible_shares==1 + "Public" in share_names when share_access present; 0 fallback when absent

**SQL injection:** `'; DROP TABLE smb_servers; --` as country → 400 (validation rejects before SQL)

**FTP/HTTP empty:** 200 with `results==[]` when protocol tables absent

**FTP/HTTP positive:** accessible_dirs==3 from ftp_probe_cache; dir_count/file_count/is_index_page/last_accessible populated from http_access

**Missing-column fallback:** accessible_dirs==0 when ftp_probe_cache missing column; all http fields default to 0/False when http_access has no summary columns

---

## Step 5 — webui/tests/test_export.py (new file)

### Key fixtures

```python
import webui.db as _db

@pytest.fixture
def real_db(tmp_path):
    # real SQLite file with smb_servers table and 1 row

@pytest.fixture
def export_dir(tmp_path):
    return tmp_path / "exports"

@pytest.fixture
def client(creds, cfg_no_tls, real_db, export_dir, monkeypatch):
    monkeypatch.setattr(_db, "_EXPORT_DIR", export_dir)  # redirect exports to tmp
    app = create_app(cfg=cfg_no_tls, creds_path=creds, db_path=real_db)
    return TestClient(app, follow_redirects=False)
```

### Test cases

**Auth/CSRF:** 303 without session; 403 without CSRF token; 403 with bad origin

**Export artifact:** 200; filename in `_EXPORT_FILENAME_RE`; no `/` in filename; file exists in export_dir; valid SQLite with smb_servers table

**Filename pattern:** `_EXPORT_FILENAME_RE.fullmatch(filename)` truthy on every export

**Uniqueness:** two rapid exports produce distinct filenames

**No-create regression:** missing source db_path → 500; no artifact created; source file still absent after call

**Download validation:** non-allowlist filename → 400; `..%2F` encoded slash → 400 or 404; valid filename → 200 with `application/octet-stream`; nonexistent valid-format filename → 404; response body has no absolute path value

---

## Step 6 — Encoded-Slash Traversal Test

Starlette may decode `%2F` before routing (→ 404) or the handler regex catches it (→ 400). Test must allow both:

```python
assert r.status_code in (400, 404)
```

---

## Step 7 — Doc Closeout

- **`README.md`**: Add `## Web UI (Optional)` section covering Results, Export, scan launch.
- **`docs/TECHNICAL_REFERENCE.md`**: Add route table (GET /results, GET /api/results/{protocol}, POST /api/export, GET /api/export/{filename}) and note on `webui/db.py` under the Optional Web UI block.
- **`docs/dev/webui/LESSONS_LEARNED.md`**: Append lessons 16–19 (VACUUM INTO contract, mode=rw no-create, per-column schema guards, two-layer download validation).

---

## Step 8 — Validation Commands

```bash
./venv/bin/python -m py_compile webui/db.py webui/app.py
./venv/bin/python -m pytest webui/tests/test_results.py webui/tests/test_export.py -q
./venv/bin/python -m pytest webui/tests/test_login.py webui/tests/test_scan_routes.py -q
```

---

## Outcome

76/76 tests pass (40 new C5 + 36 C1–C4 regression). All files within 1700-line limit.

| File | Lines |
|------|-------|
| `webui/db.py` (new) | 338 |
| `webui/app.py` (modified) | 277 |
| `webui/templates/results.html` (new) | 194 |
| `webui/tests/test_results.py` (new) | 510 |
| `webui/tests/test_export.py` (new) | 252 |
