"""Web UI FastAPI application factory."""

import ipaddress
import logging
import math
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import experimental.webui.db as _db
from experimental.webui.auth import verify_password
from experimental.webui.config import (
    TLSConfig,
    WebUIConfig,
    WebUIConfigError,
    load_config,
    save_config,
    validate,
)
from experimental.webui.dependencies import AuthRequired, get_session, same_origin, validate_csrf
from experimental.webui.sessions import Session, SessionStore, cookie_name
from experimental.webui.shodan_balance import ShodanBalanceService
from experimental.webui.tasks import CancelResult, ScanQueue, ScanRequest

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _is_ip_allowed(host, networks) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in networks)


class _LoginRequest(BaseModel):
    username: str
    password: str


class _ConfigUpdateRequest(BaseModel):
    bind_address: str
    port: int
    remote_enabled: bool
    tls_enabled: bool
    tls_allow_insecure_remote: bool
    tls_cert: str = ""
    tls_key: str = ""
    allowed_cidrs: List[str] = []
    session_timeout_idle_min: int
    session_timeout_absolute_hr: int


def health() -> dict:
    return {"status": "ok"}


def create_app(
    cfg: Optional[WebUIConfig] = None,
    creds_path=None,
    db_path=None,
    config_path=None,
    main_config_path=None,
) -> FastAPI:
    if cfg is None:
        cfg = load_config(config_path)

    app = FastAPI(
        title="Dirracuda Web UI",
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.cfg = cfg

    _networks = [ipaddress.ip_network(c, strict=False) for c in cfg.allowed_cidrs]

    @app.middleware("http")
    async def _allowlist_check(request: Request, call_next):
        if not cfg.remote_enabled:
            return await call_next(request)
        host = request.client.host if request.client else None
        if not _is_ip_allowed(host, _networks):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)
    app.state.session_store = SessionStore()
    app.state.creds_path = creds_path
    app.state.scan_queue = ScanQueue()
    app.state.db_path = Path(db_path) if db_path is not None else _db._DEFAULT_DB_PATH
    app.state.config_path = Path(config_path) if config_path is not None else None
    app.state.shodan_balance_service = ShodanBalanceService(
        main_config_path=main_config_path
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

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
        queue = request.app.state.scan_queue
        cfg_ = request.app.state.cfg
        return templates.TemplateResponse(
            request, "dashboard.html", {
                "session": session,
                "cfg": cfg_,
                "qs": queue.queue_status(),
                "active_page": "dashboard",
            }
        )

    @app.get("/api/dashboard/shodan-balance")
    async def _dashboard_shodan_balance(
        request: Request,
        session: Session = Depends(get_session),
        force: bool = Query(default=False),
    ) -> JSONResponse:
        service = request.app.state.shodan_balance_service
        try:
            payload = service.get_balance(force=force)
        except Exception:
            payload = {"state": "unavailable", "reason": "unknown", "cached": False}
        return JSONResponse(payload)

    @app.get("/scans", response_class=HTMLResponse)
    async def _scans_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "scans.html", {"session": session, "active_page": "scans"}
        )

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
        return templates.TemplateResponse(
            request, "results.html", {"session": session, "active_page": "results"}
        )

    @app.get("/api/results/details")
    async def _get_result_details(
        request: Request,
        session: Session = Depends(get_session),
        host_type: str = Query(...),
        protocol_server_id: str = Query(...),
    ) -> JSONResponse:
        host = host_type.strip().upper()
        if host not in {"S", "F", "H"}:
            return JSONResponse({"error": "host_type must be one of S, F, H"}, status_code=400)
        try:
            server_id = int(protocol_server_id)
        except (TypeError, ValueError):
            return JSONResponse({"error": "protocol_server_id must be a positive integer"}, status_code=400)
        if server_id <= 0:
            return JSONResponse({"error": "protocol_server_id must be a positive integer"}, status_code=400)

        db_path = request.app.state.db_path
        try:
            payload = _db.get_result_details(db_path, host, server_id)
        except Exception:
            logger.exception(
                "results details query failed: host_type=%s protocol_server_id=%s",
                host,
                server_id,
            )
            return JSONResponse({"error": "database error"}, status_code=500)
        if payload is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(payload)

    @app.get("/api/results/{protocol}")
    async def _get_results(
        protocol: Literal["all", "smb", "ftp", "http"],
        request: Request,
        session: Session = Depends(get_session),
        page: int = Query(default=1),
        page_size: int = Query(default=_db._PAGE_SIZE_DEFAULT),
        country: Optional[str] = Query(default=None),
        shares_only: bool = Query(default=False),
        favorites_only: bool = Query(default=False),
        hide_avoid: bool = Query(default=False),
    ) -> JSONResponse:
        try:
            p, ps, c = _db._validate_bounds(page, page_size, country)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        db_path = request.app.state.db_path
        try:
            rows, total_count = _db.get_results_table_rows(
                db_path,
                protocol,
                p,
                ps,
                c,
                shares_only=shares_only,
                favorites_only=favorites_only,
                hide_avoid=hide_avoid,
            )
        except Exception:
            logger.exception("results query failed: protocol=%s", protocol)
            return JSONResponse({"error": "database error"}, status_code=500)
        total_pages = max(1, math.ceil(total_count / ps)) if ps > 0 else 1
        return JSONResponse(
            {
                "results": rows,
                "page": p,
                "page_size": ps,
                "total_count": total_count,
                "total_pages": total_pages,
            }
        )

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

    @app.get("/config", response_class=HTMLResponse)
    async def _config_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "config.html", {
                "session": session,
                "cfg": request.app.state.cfg,
                "active_page": "config",
            }
        )

    @app.post("/config")
    async def _config_save(
        body: _ConfigUpdateRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        new_cfg = WebUIConfig(
            enabled=request.app.state.cfg.enabled,
            bind_address=body.bind_address,
            port=body.port,
            remote_enabled=body.remote_enabled,
            allowed_cidrs=list(body.allowed_cidrs),
            session_timeout_idle=body.session_timeout_idle_min * 60,
            session_timeout_absolute=body.session_timeout_absolute_hr * 3600,
            tls=TLSConfig(
                enabled=body.tls_enabled,
                cert_file=body.tls_cert,
                key_file=body.tls_key,
                allow_insecure_remote=body.tls_allow_insecure_remote,
            ),
        )
        try:
            validate(new_cfg)
        except WebUIConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            save_config(new_cfg, path=request.app.state.config_path)
        except Exception:
            logger.exception("config save failed")
            return JSONResponse({"error": "failed to save config"}, status_code=500)
        logger.info("config saved by user=%r", session.username)
        return JSONResponse({"ok": True, "note": "Changes take effect on restart."})

    return app
