"""Web UI FastAPI application factory."""

import logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from webui.auth import verify_password
from webui.config import WebUIConfig, load_config
from webui.dependencies import AuthRequired, get_session, same_origin, validate_csrf
from webui.sessions import Session, SessionStore, cookie_name

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

    return app
