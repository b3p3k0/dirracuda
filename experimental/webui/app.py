"""Web UI FastAPI application factory."""

import ipaddress
import logging
import math
from pathlib import Path
from typing import Any, List, Literal, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from experimental.dorkbook import store as dork_store
from experimental.dorkbook.models import ROW_KIND_BUILTIN, DuplicateEntryError, ReadOnlyEntryError
from experimental.redseek.service import IngestOptions as RedditIngestOptions, run_ingest
from experimental.se_dork.client import run_preflight as run_searxng_preflight
from experimental.se_dork.models import RUN_STATUS_DONE as SEARXNG_RUN_STATUS_DONE
from experimental.se_dork.models import RunOptions as SearxngRunOptions
from experimental.se_dork.service import run_dork_search
import experimental.webui.db as _db
import experimental.webui.db_actions as _db_actions
from experimental.webui.experimental_features import (
    load_reddit_rows,
    load_searxng_rows,
    probe_reddit_rows,
    probe_searxng_rows,
    promote_reddit_rows,
    promote_searxng_rows,
)
from experimental.webui.experimental_models import (
    DorkbookEntryCreateRequest,
    DorkbookPrefillRequest,
    RedditActionRequest,
    RedditRunRequest,
    SearxngActionRequest,
    SearxngPreflightRequest,
    SearxngRunRequest,
)
from gui.components.discovery_dork_config import apply_discovery_dorks, read_discovery_dorks
import experimental.webui.results_probe_actions as _results_probe_actions
from experimental.webui.auth import (
    BlocklistUnavailableError, CredentialError, check_credential_store,
    set_password, verify_password,
)
from experimental.webui.config import (
    AuthConfig,
    TLSConfig,
    WebUIConfig,
    WebUIConfigError,
    load_config,
    save_config,
    validate,
)
from experimental.webui.rate_limiter import (
    NullRateLimiter,
    RateLimiter,
    RateLimiterInitError,
    RateLimiterRuntimeError,
    _DEFAULT_RL_PATH,
)
from experimental.webui.shared_jobs import SharedCancelResult, SharedJobQueue
from experimental.webui.dependencies import AuthRequired, get_session, same_origin, validate_csrf
from experimental.webui.sessions import Session, SessionStore, cookie_name
from experimental.webui.shodan_balance import ShodanBalanceService
from experimental.webui.tasks import (
    CancelResult,
    ScanQueue,
    ScanRequest,
    estimate_query_credits_by_protocol,
)
from shared.config import load_config as load_main_config
from shared.db_path_resolution import normalize_database_path
from shared.path_service import get_paths

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
_PATHS = get_paths()

_DORKBOOK_PROTOCOL_TO_FIELD = {
    "SMB": "smb_dork",
    "FTP": "ftp_dork",
    "HTTP": "http_dork",
}

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


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


class _ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


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
    auth_lockout_threshold: Optional[int] = None
    auth_lockout_window_sec: Optional[int] = None
    auth_lockout_base_duration_sec: Optional[int] = None
    auth_lockout_max_duration_sec: Optional[int] = None


class _ScansPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocols: list[Literal["smb", "ftp", "http"]]
    max_shodan_results: int

    @field_validator("protocols")
    @classmethod
    def _validate_protocols(
        cls, value: list[Literal["smb", "ftp", "http"]]
    ) -> list[Literal["smb", "ftp", "http"]]:
        if len(value) < 1 or len(value) > 3:
            raise ValueError("protocols must contain 1 to 3 values")
        if len(set(value)) != len(value):
            raise ValueError("protocols must be unique")
        return value

    @field_validator("max_shodan_results")
    @classmethod
    def _validate_max_shodan_results(cls, value: int) -> int:
        if value < 1 or value > 100000:
            raise ValueError("max_shodan_results must be between 1 and 100000")
        return value


class _ToggleActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    host_type: Literal["S", "F", "H"]
    protocol_server_id: int
    row_key: Optional[str] = None

    @field_validator("protocol_server_id")
    @classmethod
    def _validate_protocol_server_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("protocol_server_id must be a positive integer")
        return value


class _ToggleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["favorite", "avoid", "compromised"]
    targets: list[_ToggleActionTarget]

    @field_validator("targets")
    @classmethod
    def _validate_targets(cls, value: list[_ToggleActionTarget]) -> list[_ToggleActionTarget]:
        if len(value) < 1:
            raise ValueError("targets must contain at least 1 row")
        if len(value) > 200:
            raise ValueError("targets must not exceed 200 rows")
        return value


class _ProbeActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    targets: list[_ToggleActionTarget]

    @field_validator("targets")
    @classmethod
    def _validate_targets(cls, value: list[_ToggleActionTarget]) -> list[_ToggleActionTarget]:
        if len(value) < 1:
            raise ValueError("targets must contain at least 1 row")
        if len(value) > 200:
            raise ValueError("targets must not exceed 200 rows")
        return value


def health() -> dict:
    return {"status": "ok"}


def _resolve_main_config_path(main_config_path: Optional[Path]) -> Path:
    candidate = Path(main_config_path).expanduser() if main_config_path is not None else _PATHS.config_file
    return candidate.resolve(strict=False)


def _resolve_db_path_from_main_config(main_config_path: Path) -> Path:
    try:
        cfg = load_main_config(str(main_config_path))
        preferred = cfg.get_database_path()
    except Exception as exc:
        logger.warning(
            "webui main-config database path resolution failed; using default: %s", exc
        )
        return _db._DEFAULT_DB_PATH

    normalized = normalize_database_path(preferred, _PATHS.repo_root)
    if normalized is None:
        logger.warning(
            "webui main-config database path was invalid (%r); using default",
            preferred,
        )
        return _db._DEFAULT_DB_PATH
    return normalized


def create_app(
    cfg: Optional[WebUIConfig] = None,
    creds_path=None,
    db_path=None,
    config_path=None,
    main_config_path=None,
    rl_db_path=None,
    keymaster_db_path=None,
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
    resolved_main_config_path = _resolve_main_config_path(main_config_path)
    if db_path is not None:
        resolved_db_path = Path(db_path).expanduser().resolve(strict=False)
        db_path_source = "override"
    else:
        resolved_db_path = _resolve_db_path_from_main_config(resolved_main_config_path)
        db_path_source = "main_config"

    _networks = [ipaddress.ip_network(c, strict=False) for c in cfg.allowed_cidrs]

    @app.middleware("http")
    async def _allowlist_check(request: Request, call_next):
        if not cfg.remote_enabled:
            return await call_next(request)
        host = request.client.host if request.client else None
        if not _is_ip_allowed(host, _networks):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = _CSP_POLICY
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    resolved_km_db_path = (
        Path(keymaster_db_path).expanduser().resolve(strict=False)
        if keymaster_db_path is not None
        else None
    )

    app.state.session_store = SessionStore()
    app.state.creds_path = creds_path
    app.state.scan_queue = ScanQueue(base_config_path=resolved_main_config_path)
    app.state.shared_jobs = SharedJobQueue(app.state.scan_queue)
    app.state.db_path = resolved_db_path
    app.state.main_config_path = resolved_main_config_path
    app.state.config_path = Path(config_path) if config_path is not None else None
    app.state.keymaster_db_path = resolved_km_db_path
    app.state.shodan_balance_service = ShodanBalanceService(
        main_config_path=resolved_main_config_path
    )
    app.state.results_probe_jobs = _results_probe_actions.ResultsProbeJobManager(
        db_path=app.state.db_path,
        main_config_path=resolved_main_config_path,
    )
    logger.info(
        "webui path resolution: main_config=%s db_path=%s source=%s",
        resolved_main_config_path,
        resolved_db_path,
        db_path_source,
    )
    rl_path = Path(rl_db_path) if rl_db_path is not None else _DEFAULT_RL_PATH
    try:
        app.state.rate_limiter = RateLimiter(rl_path, cfg.auth)
    except RateLimiterInitError as exc:
        if cfg.remote_enabled:
            logger.error(
                "Rate limiter init failed in remote mode — startup refused: %s", exc
            )
            raise
        logger.error(
            "Rate limiter init failed, starting in degraded mode (localhost): %s", exc
        )
        app.state.rate_limiter = NullRateLimiter()

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.exception_handler(AuthRequired)
    async def _auth_redirect(request: Request, exc: AuthRequired) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/health")
    def _health(request: Request) -> dict:
        d = dict(health())
        rl = request.app.state.rate_limiter
        d["rate_limiter"] = rl.health_check()
        return d

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
        rl = request.app.state.rate_limiter
        client_ip = (request.client.host or "unknown") if request.client else "unknown"
        try:
            locked, _ = rl.check_locked(body.username, client_ip)
        except RateLimiterRuntimeError as exc:
            logger.error("rate limiter check_locked failed: %s", exc)
            if cfg_.remote_enabled:
                return JSONResponse({"error": "Service temporarily unavailable."}, status_code=503)
            locked = False  # localhost degraded: proceed unthrottled
        if locked:
            logger.warning(
                "login blocked by lockout: username=%r ip=%r", body.username, client_ip
            )
            return JSONResponse({"error": "Invalid username or password."}, status_code=401)
        if not verify_password(body.username, body.password, path=creds):
            try:
                rl.record_failure(body.username, client_ip)
            except RateLimiterRuntimeError as exc:
                logger.error("rate limiter record_failure failed: %s", exc)
                if cfg_.remote_enabled:
                    return JSONResponse({"error": "Service temporarily unavailable."}, status_code=503)
                # localhost: failure not recorded; already returning 401
            logger.warning("login failed: username=%r ip=%r", body.username, client_ip)
            return JSONResponse({"error": "Invalid username or password."}, status_code=401)
        rl.record_success(body.username)
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

    @app.get("/account", response_class=HTMLResponse)
    async def _account_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "account.html", {"session": session, "active_page": "account"}
        )

    @app.post("/api/auth/change-password")
    async def _change_password(
        body: _ChangePasswordRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        creds = request.app.state.creds_path
        try:
            check_credential_store(creds)
        except CredentialError as exc:
            logger.error("credential store permission error during change-password: %s", exc)
            return JSONResponse({"error": "Credential store configuration error."}, status_code=503)
        if not verify_password(session.username, body.current_password, path=creds):
            logger.warning("change-password failed: username=%r", session.username)
            return JSONResponse({"error": "Current password is incorrect."}, status_code=401)
        try:
            set_password(session.username, body.new_password, path=creds)
        except BlocklistUnavailableError as exc:
            logger.error("blocklist unavailable during change-password: %s", exc)
            return JSONResponse({"error": "Service configuration error."}, status_code=503)
        except CredentialError as exc:
            logger.error("credential store permission error (TOCTOU) during change-password: %s", exc)
            return JSONResponse({"error": "Credential store configuration error."}, status_code=503)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        logger.info("password changed: username=%r", session.username)
        return JSONResponse({"ok": True})

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

    @app.get("/scans/shodan", response_class=HTMLResponse)
    async def _scans_shodan_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "scans.html", {"session": session, "active_page": "scans_shodan"}
        )

    @app.get("/scans/searxng", response_class=HTMLResponse)
    async def _scans_searxng_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "searxng.html", {"session": session, "active_page": "scans_searxng"}
        )

    @app.get("/scans/reddit", response_class=HTMLResponse)
    async def _scans_reddit_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "reddit.html", {"session": session, "active_page": "scans_reddit"}
        )

    @app.post("/api/searxng/preflight")
    async def _searxng_preflight(
        body: SearxngPreflightRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            result = run_searxng_preflight(body.instance_url)
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "reason_code": "unknown", "message": str(exc)},
                status_code=200,
            )
        return JSONResponse(
            {
                "ok": bool(result.ok),
                "reason_code": result.reason_code,
                "message": result.message,
            }
        )

    @app.post("/api/searxng/run")
    async def _searxng_run(
        body: SearxngRunRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        shared_jobs = request.app.state.shared_jobs
        config_path = str(request.app.state.main_config_path)

        def _runner(job):
            job.set_progress("Running SearXNG discovery...", 5.0)
            options = SearxngRunOptions(
                instance_url=body.instance_url,
                query=body.query,
                max_results=body.max_results,
                bulk_probe_enabled=body.bulk_probe_enabled,
                probe_config_path=config_path,
                probe_worker_count=body.probe_worker_count,
            )
            result = run_dork_search(options)
            if result.status != SEARXNG_RUN_STATUS_DONE:
                raise RuntimeError(result.error or "SearXNG run failed")
            job.set_metadata(
                run_id=result.run_id,
                fetched_count=result.fetched_count,
                retained_count=result.deduped_count,
                verified_count=result.verified_count,
                probe_total=result.probe_total,
                probe_clean=result.probe_clean,
                probe_issue=result.probe_issue,
                probe_unprobed=result.probe_unprobed,
            )
            job.set_progress(
                f"SearXNG run complete: {result.deduped_count} retained row(s).",
                100.0,
            )

        queued = shared_jobs.submit_external(
            source="searxng",
            kind="run",
            label=f"SearXNG run: {body.query[:60]}",
            runner=_runner,
            metadata={"query": body.query, "max_results": body.max_results},
        )
        return JSONResponse(
            {"job_id": queued.job_id, "status": queued.status.value},
            status_code=202,
        )

    @app.get("/api/searxng/results")
    async def _searxng_results(
        request: Request,
        session: Session = Depends(get_session),
        search: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> JSONResponse:
        try:
            rows = load_searxng_rows(None)
        except Exception as exc:
            logger.exception("searxng results load failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        search_norm = str(search or "").strip().lower()
        if search_norm:
            rows = [
                row for row in rows
                if search_norm in str(row.get("url") or "").lower()
                or search_norm in str(row.get("probe_preview") or "").lower()
                or search_norm in str(row.get("verdict") or "").lower()
            ]

        rows = rows[:limit]
        return JSONResponse({"results": rows, "count": len(rows)})

    @app.post("/api/searxng/actions/probe")
    async def _searxng_probe(
        body: SearxngActionRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        shared_jobs = request.app.state.shared_jobs
        config_path = str(request.app.state.main_config_path)
        result_ids = list(body.result_ids)

        def _runner(job):
            job.set_progress("Starting SearXNG probe batch...", 2.0)

            def _progress(done: int, total: int, message: str) -> None:
                pct = (float(done) / float(total) * 100.0) if total else 100.0
                job.set_progress(message, pct)

            summary = probe_searxng_rows(
                None,
                result_ids,
                config_path=config_path,
                cancel_event=job.cancel_event,
                progress_callback=_progress,
            )
            job.set_metadata(summary=summary)
            job.set_progress(
                f"SearXNG probe complete: {summary.get('processed', 0)} row(s) processed.",
                100.0,
            )

        queued = shared_jobs.submit_external(
            source="searxng",
            kind="probe",
            label=f"SearXNG probe batch ({len(result_ids)} rows)",
            runner=_runner,
            metadata={"result_ids": result_ids},
        )
        return JSONResponse(
            {"job_id": queued.job_id, "status": queued.status.value},
            status_code=202,
        )

    @app.post("/api/searxng/actions/promote")
    async def _searxng_promote(
        body: SearxngActionRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            rows = load_searxng_rows(None)
            summary = promote_searxng_rows(request.app.state.db_path, rows, body.result_ids)
        except Exception as exc:
            logger.exception("searxng promote failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(summary)

    @app.post("/api/reddit/run")
    async def _reddit_run(
        body: RedditRunRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        shared_jobs = request.app.state.shared_jobs
        config_path = str(request.app.state.main_config_path)
        request_data = body.model_dump()

        def _runner(job):
            job.set_progress("Running Reddit ingest...", 5.0)
            options = RedditIngestOptions(
                sort=request_data["sort"],
                max_posts=request_data["max_posts"],
                parse_body=request_data["parse_body"],
                include_nsfw=request_data["include_nsfw"],
                replace_cache=request_data["replace_cache"],
                max_pages=request_data["max_pages"],
                subreddit="opendirectories",
                top_window=request_data["top_window"],
                mode=request_data["mode"],
                query=request_data["query"],
                username=request_data["username"],
                bulk_probe_enabled=request_data["bulk_probe_enabled"],
                probe_config_path=config_path,
                probe_worker_count=request_data.get("probe_worker_count"),
            )
            result = run_ingest(options)
            if result.error:
                raise RuntimeError(result.error)
            job.set_metadata(
                pages_fetched=result.pages_fetched,
                posts_stored=result.posts_stored,
                posts_skipped=result.posts_skipped,
                targets_stored=result.targets_stored,
                targets_deduped=result.targets_deduped,
                probe_total=result.probe_total,
                probe_clean=result.probe_clean,
                probe_issue=result.probe_issue,
                probe_unprobed=result.probe_unprobed,
                probe_skipped=result.probe_skipped,
            )
            job.set_progress(
                f"Reddit ingest complete: {result.targets_stored} target(s) stored.",
                100.0,
            )

        queued = shared_jobs.submit_external(
            source="reddit",
            kind="run",
            label=f"Reddit {body.mode} run ({body.sort})",
            runner=_runner,
            metadata=request_data,
        )
        return JSONResponse(
            {"job_id": queued.job_id, "status": queued.status.value},
            status_code=202,
        )

    @app.get("/api/reddit/results")
    async def _reddit_results(
        request: Request,
        session: Session = Depends(get_session),
        search: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> JSONResponse:
        try:
            rows = load_reddit_rows(None)
        except Exception as exc:
            logger.exception("reddit results load failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        search_norm = str(search or "").strip().lower()
        if search_norm:
            rows = [
                row for row in rows
                if search_norm in str(row.get("target_normalized") or "").lower()
                or search_norm in str(row.get("host") or "").lower()
                or search_norm in str(row.get("post_author") or "").lower()
                or search_norm in str(row.get("post_title") or "").lower()
                or search_norm in str(row.get("probe_preview") or "").lower()
            ]
        rows = rows[:limit]
        return JSONResponse({"results": rows, "count": len(rows)})

    @app.post("/api/reddit/actions/probe")
    async def _reddit_probe(
        body: RedditActionRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        shared_jobs = request.app.state.shared_jobs
        config_path = str(request.app.state.main_config_path)
        target_ids = list(body.target_ids)

        def _runner(job):
            job.set_progress("Starting Reddit probe batch...", 2.0)

            def _progress(done: int, total: int, message: str) -> None:
                pct = (float(done) / float(total) * 100.0) if total else 100.0
                job.set_progress(message, pct)

            summary = probe_reddit_rows(
                None,
                target_ids,
                config_path=config_path,
                cancel_event=job.cancel_event,
                progress_callback=_progress,
            )
            job.set_metadata(summary=summary)
            job.set_progress(
                f"Reddit probe complete: {summary.get('processed', 0)} row(s) processed.",
                100.0,
            )

        queued = shared_jobs.submit_external(
            source="reddit",
            kind="probe",
            label=f"Reddit probe batch ({len(target_ids)} rows)",
            runner=_runner,
            metadata={"target_ids": target_ids},
        )
        return JSONResponse(
            {"job_id": queued.job_id, "status": queued.status.value},
            status_code=202,
        )

    @app.post("/api/reddit/actions/promote")
    async def _reddit_promote(
        body: RedditActionRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            rows = load_reddit_rows(None)
            summary = promote_reddit_rows(request.app.state.db_path, rows, body.target_ids)
        except Exception as exc:
            logger.exception("reddit promote failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(summary)

    @app.post("/api/scans/preflight")
    async def _scans_preflight(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        try:
            parsed = _ScansPreflightRequest.model_validate(body)
        except ValidationError:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        service = request.app.state.shodan_balance_service
        credits_by_protocol = estimate_query_credits_by_protocol(
            parsed.protocols, parsed.max_shodan_results
        )
        estimated_total = sum(credits_by_protocol.values())
        try:
            balance = service.get_balance(force=True)
        except Exception:
            balance = {"state": "unavailable", "reason": "unknown", "cached": False}

        estimated_post_scan_balance: Optional[int] = None
        if balance.get("state") == "ok":
            try:
                estimated_post_scan_balance = int(balance.get("query_credits")) - estimated_total
            except Exception:
                estimated_post_scan_balance = None

        return JSONResponse(
            {
                "estimated_query_credits_total": estimated_total,
                "estimated_query_credits_by_protocol": credits_by_protocol,
                "balance": balance,
                "estimated_post_scan_balance": estimated_post_scan_balance,
                "dashboard_url": "https://developer.shodan.io/dashboard",
            }
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

    @app.get("/api/scans")
    async def _get_scans_queue(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        queue = request.app.state.scan_queue
        return JSONResponse(queue.queue_status())

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

    @app.get("/api/jobs")
    async def _get_jobs_queue(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        shared_jobs = request.app.state.shared_jobs
        return JSONResponse(shared_jobs.queue_status())

    @app.get("/api/jobs/{job_id}")
    async def _get_job(
        job_id: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        shared_jobs = request.app.state.shared_jobs
        payload = shared_jobs.get_job(job_id)
        if payload is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(payload)

    @app.post("/api/jobs/{job_id}/cancel")
    async def _cancel_job(
        job_id: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        shared_jobs = request.app.state.shared_jobs
        result = shared_jobs.cancel(job_id)
        if result == SharedCancelResult.NOT_FOUND:
            return JSONResponse({"error": "not found"}, status_code=404)
        if result == SharedCancelResult.TERMINAL:
            payload = shared_jobs.get_job(job_id)
            status = payload.get("status") if isinstance(payload, dict) else "unknown"
            return JSONResponse({"ok": False, "status": status}, status_code=409)
        logger.info("job cancel requested: job_id=%s", job_id)
        return JSONResponse({"ok": True})

    @app.get("/results", response_class=HTMLResponse)
    async def _results_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "results.html", {"session": session, "active_page": "results"}
        )

    @app.get("/export", response_class=HTMLResponse)
    async def _export_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "export.html", {"session": session, "active_page": "export"}
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

    @app.post("/api/results/actions/toggle")
    async def _toggle_results_actions(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        try:
            parsed = _ToggleActionRequest.model_validate(body)
        except ValidationError:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        db_path = request.app.state.db_path
        targets = [
            {
                "host_type": item.host_type,
                "protocol_server_id": item.protocol_server_id,
                "row_key": item.row_key,
            }
            for item in parsed.targets
        ]
        try:
            payload = _db_actions.toggle_results_actions(db_path, parsed.action, targets)
        except Exception:
            logger.exception("results toggle action failed: action=%s", parsed.action)
            return JSONResponse({"error": "database error"}, status_code=500)
        return JSONResponse(payload)

    @app.post("/api/results/actions/probe")
    async def _start_results_probe_action(
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

        try:
            body: Any = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        try:
            parsed = _ProbeActionRequest.model_validate(body)
        except ValidationError:
            return JSONResponse({"error": "invalid payload"}, status_code=400)

        targets = [
            {
                "host_type": item.host_type,
                "protocol_server_id": item.protocol_server_id,
                "row_key": item.row_key,
            }
            for item in parsed.targets
        ]

        manager = request.app.state.results_probe_jobs
        try:
            payload = manager.start_job(targets)
        except _results_probe_actions.ProbeJobConflictError as exc:
            return JSONResponse(
                {
                    "error": "probe job already running",
                    "job_id": exc.job_id,
                    "poll_url": f"/api/results/actions/probe/{exc.job_id}",
                },
                status_code=409,
            )
        except Exception:
            logger.exception("results probe action failed to start")
            return JSONResponse({"error": "probe start failed"}, status_code=500)

        return JSONResponse(payload, status_code=202)

    @app.get("/api/results/actions/probe/{job_id}")
    async def _get_results_probe_action(
        job_id: str,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        manager = request.app.state.results_probe_jobs
        payload = manager.get_job(job_id)
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
        search: Optional[str] = Query(default=None),
        shares_only: bool = Query(default=False),
        favorites_only: bool = Query(default=False),
        hide_avoid: bool = Query(default=False),
    ) -> JSONResponse:
        if "country" in request.query_params:
            return JSONResponse(
                {"error": "country filter has been removed; use search instead"},
                status_code=400,
            )
        try:
            p, ps, _ = _db._validate_bounds(page, page_size, None)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        db_path = request.app.state.db_path
        try:
            rows, total_count = _db.get_results_table_rows(
                db_path,
                protocol,
                p,
                ps,
                search,
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

    @app.get("/extras/dorkbook", response_class=HTMLResponse)
    async def _extras_dorkbook_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "dorkbook.html", {"session": session, "active_page": "extras_dorkbook"}
        )

    @app.get("/api/dorkbook/entries")
    async def _dorkbook_list_entries(
        request: Request,
        protocol: Literal["SMB", "FTP", "HTTP"] = Query("SMB"),
        search: str = Query(""),
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        try:
            dork_store.init_db(_PATHS.dorkbook_db_file)
            with dork_store.open_connection(_PATHS.dorkbook_db_file) as conn:
                rows = dork_store.list_entries(conn, protocol, search_text=search)
        except Exception as exc:
            logger.warning("dorkbook list_entries failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"entries": rows})

    @app.post("/api/dorkbook/entries", status_code=201)
    async def _dorkbook_create_entry(
        body: DorkbookEntryCreateRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            dork_store.init_db(_PATHS.dorkbook_db_file)
            with dork_store.open_connection(_PATHS.dorkbook_db_file) as conn:
                entry_id = dork_store.create_entry(
                    conn,
                    protocol=body.protocol,
                    nickname=body.nickname,
                    query=body.query,
                    notes=body.notes,
                )
                conn.commit()
        except DuplicateEntryError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:
            logger.warning("dorkbook create_entry failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"entry_id": entry_id, "ok": True}, status_code=201)

    @app.delete("/api/dorkbook/entries/{entry_id}")
    async def _dorkbook_delete_entry(
        entry_id: int,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            dork_store.init_db(_PATHS.dorkbook_db_file)
            with dork_store.open_connection(_PATHS.dorkbook_db_file) as conn:
                row = dork_store.get_entry(conn, entry_id)
                if row is None:
                    return JSONResponse({"error": "entry not found"}, status_code=404)
                if str(row.get("row_kind") or "").strip().lower() == ROW_KIND_BUILTIN:
                    return JSONResponse({"error": "built-in dorks are read-only"}, status_code=403)
                try:
                    dork_store.delete_entry(conn, entry_id)
                except ReadOnlyEntryError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=403)
                conn.commit()
        except Exception as exc:
            logger.warning("dorkbook delete_entry failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True})

    @app.post("/api/dorkbook/prefill")
    async def _dorkbook_prefill(
        body: DorkbookPrefillRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> JSONResponse:
        if not same_origin(request):
            return JSONResponse({"error": "origin check failed"}, status_code=403)
        csrf_tok = request.headers.get("X-CSRF-Token")
        if not validate_csrf(csrf_tok, session.csrf_token):
            return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
        try:
            dork_store.init_db(_PATHS.dorkbook_db_file)
            with dork_store.open_connection(_PATHS.dorkbook_db_file) as conn:
                row = dork_store.get_entry(conn, body.entry_id)
        except Exception as exc:
            logger.warning("dorkbook prefill: store access failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        if row is None:
            return JSONResponse({"error": "entry not found"}, status_code=404)
        protocol = str(row.get("protocol") or "").strip().upper()
        field = _DORKBOOK_PROTOCOL_TO_FIELD.get(protocol)
        if field is None:
            return JSONResponse(
                {"error": f"unsupported protocol in dorkbook entry: {protocol!r}"},
                status_code=422,
            )
        query = str(row.get("query") or "").strip()
        main_config_path: Path = request.app.state.main_config_path
        try:
            if main_config_path.resolve(strict=False) == _PATHS.config_file.resolve(strict=False):
                cfg = load_main_config(str(main_config_path))
                config_data = {
                    "shodan": cfg.get("shodan", default={}) or {},
                    "ftp": cfg.get("ftp", default={}) or {},
                    "http": cfg.get("http", default={}) or {},
                }
                current = read_discovery_dorks(config_data)
                current[field] = query
                apply_discovery_dorks(config_data, current)
                updates = {
                    s: config_data[s]
                    for s in ("shodan", "ftp", "http")
                    if isinstance(config_data.get(s), dict)
                }
                if not cfg.update_sections(updates):
                    raise RuntimeError("failed to persist discovery dork sections")
            else:
                if not main_config_path.is_file():
                    return JSONResponse({"error": "config file not found"}, status_code=404)
                import json as _json
                data = _json.loads(main_config_path.read_text(encoding="utf-8"))
                current = read_discovery_dorks(data)
                current[field] = query
                apply_discovery_dorks(data, current)
                main_config_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("dorkbook prefill: config write failed: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"ok": True, "protocol": protocol})

    @app.get("/extras/keymaster", response_class=HTMLResponse)
    async def _extras_keymaster_page(
        request: Request,
        session: Session = Depends(get_session),
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "keymaster.html", {"session": session, "active_page": "extras_keymaster"}
        )

    from experimental.webui.keymaster_routes import keymaster_router
    app.include_router(keymaster_router)

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

        existing_auth = request.app.state.cfg.auth
        new_auth = AuthConfig(
            lockout_threshold=body.auth_lockout_threshold
                if body.auth_lockout_threshold is not None
                else existing_auth.lockout_threshold,
            lockout_window_sec=body.auth_lockout_window_sec
                if body.auth_lockout_window_sec is not None
                else existing_auth.lockout_window_sec,
            lockout_base_duration_sec=body.auth_lockout_base_duration_sec
                if body.auth_lockout_base_duration_sec is not None
                else existing_auth.lockout_base_duration_sec,
            lockout_max_duration_sec=body.auth_lockout_max_duration_sec
                if body.auth_lockout_max_duration_sec is not None
                else existing_auth.lockout_max_duration_sec,
        )
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
            auth=new_auth,
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
