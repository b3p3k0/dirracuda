"""Web UI FastAPI application factory."""

import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import webui.db as _db
from webui.auth import verify_password
from webui.config import WebUIConfig, load_config
from webui.dependencies import AuthRequired, get_session, same_origin, validate_csrf
from webui.sessions import Session, SessionStore, cookie_name
from webui.tasks import CancelResult, ScanQueue, ScanRequest

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class _LoginRequest(BaseModel):
    username: str
    password: str


def health() -> dict:
    return {"status": "ok"}


def create_app(
    cfg: Optional[WebUIConfig] = None,
    creds_path=None,
    db_path=None,
) -> FastAPI:
    if cfg is None:
        cfg = load_config()

    app = FastAPI(
        title="Dirracuda Web UI",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.cfg = cfg
    app.state.session_store = SessionStore()
    app.state.creds_path = creds_path
    app.state.scan_queue = ScanQueue()
    app.state.db_path = Path(db_path) if db_path is not None else _db._DEFAULT_DB_PATH

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.exception_handler(AuthRequired)
    async def _auth_redirect(request: Request, exc: AuthRequired) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/health")
    def _health() -> dict:
        return health()

    @app.get("/")
    async def _root() -> RedirectResponse:
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def _login_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "login.html")

    @app.post("/login")
    async def _login_submit(body: _LoginRequest, request: Request) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        cfg_ = request.app.state.cfg
        store = request.app.state.session_store
        creds = request.app.state.creds_path
        if not verify_password(body.username, body.password, path=creds):
            logger.warning("login failed: username=%r", body.username)
            return JSONResponse({"error": "Invalid username or password."}, status_code=401)
        sid, _ = store.create(body.username)
        logger.info("login success: username=%r", body.username)
        tls_on = cfg_.tls.enabled
        name = cookie_name(tls_on)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            key=name,
            value=sid,
            httponly=True,
            samesite="strict",
            path="/",
            secure=tls_on,
        )
        return response

    @app.post("/logout")
    async def _logout(request: Request) -> JSONResponse:
        cfg_ = request.app.state.cfg
        store = request.app.state.session_store
        tls_on = cfg_.tls.enabled
        name = cookie_name(tls_on)
        sid = request.cookies.get(name)

        response = JSONResponse({"ok": True})

        def _clear(resp: JSONResponse) -> JSONResponse:
            resp.delete_cookie(
                key=name, path="/", secure=tls_on, httponly=True, samesite="strict"
            )
            return resp

        if not sid:
            return _clear(response)

        s = store.get(sid, cfg_.session_timeout_idle, cfg_.session_timeout_absolute)
        if s is None:
            return _clear(response)

        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, s.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        store.delete(sid)
        logger.info("logout: username=%r", s.username)
        return _clear(response)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def _dashboard(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "dashboard.html", {"session": session}
        )

    @app.get("/scans", response_class=HTMLResponse)
    async def _scans_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(request, "scans.html", {"session": session})

    @app.post("/api/scans")
    async def _submit_scan(
        body: ScanRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        queue = request.app.state.scan_queue
        task = queue.submit(body)
        logger.info("scan submitted: task_id=%s protocol=%s", task.task_id, body.protocol)
        return JSONResponse(
            {"task_id": task.task_id, "status": task.status.value}, status_code=202
        )

    @app.get("/api/scans/{task_id}")
    async def _get_scan(
        task_id: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        queue = request.app.state.scan_queue
        task = queue.get_task(task_id)
        if task is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(task.to_dict())

    @app.post("/api/scans/{task_id}/cancel")
    async def _cancel_scan(
        task_id: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        queue = request.app.state.scan_queue
        result = queue.cancel(task_id)
        if result == CancelResult.NOT_FOUND:
            return JSONResponse({"error": "not found"}, status_code=404)
        if result == CancelResult.TERMINAL:
            task = queue.get_task(task_id)
            status = task.status.value if task else "unknown"
            return JSONResponse({"ok": False, "status": status}, status_code=409)

        logger.info("scan cancel requested: task_id=%s", task_id)
        return JSONResponse({"ok": True})

    @app.get("/results", response_class=HTMLResponse)
    async def _results_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(request, "results.html", {"session": session})

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

    @app.post("/api/export")
    async def _trigger_export(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
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
        return JSONResponse({"filename": artifact.name})

    @app.get("/api/export/{filename}")
    async def _download_export(
        filename: str,
        request: Request,
        session: Session = Depends(get_session),
    ):
        if not _db._EXPORT_FILENAME_RE.fullmatch(filename):
            return JSONResponse({"error": "invalid filename"}, status_code=400)
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
        return FileResponse(
            path=target,
            filename=filename,
            media_type="application/octet-stream",
        )

    return app
