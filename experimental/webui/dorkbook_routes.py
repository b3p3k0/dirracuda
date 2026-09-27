"""Authenticated unified Dorkbook API; defaults are shared with the desktop."""

from contextlib import closing
import logging
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from experimental.dorkbook import defaults, store
from experimental.dorkbook.models import DuplicateEntryError, ReadOnlyEntryError
from experimental.webui.dependencies import get_session, same_origin, validate_csrf
from experimental.webui.experimental_models import DorkbookEntryCreateRequest, DorkbookPrefillRequest
from experimental.webui.sessions import Session
from shared.path_service import get_paths

logger = logging.getLogger(__name__)
dorkbook_router = APIRouter()


def _db_path(request):
    return getattr(request.app.state, "dorkbook_db_path", get_paths().dorkbook_db_file)


def _security_error(request, session):
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    if not validate_csrf(request.headers.get("X-CSRF-Token"), session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)
    return None


def _log_failure(operation, exc):
    logger.warning("dorkbook %s failed: exception_class=%s", operation, type(exc).__name__)


@dorkbook_router.get("/api/dorkbook/entries")
async def list_entries(
    request: Request,
    protocol: Optional[Literal["SMB", "FTP", "HTTP"]] = Query(None),
    provider: Optional[Literal["shodan", "self_hosted"]] = Query(None),
    search: str = Query(""),
    topic: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    if provider == "self_hosted" and protocol is not None:
        return JSONResponse({"error": "Self-hosted Search has no protocol"}, status_code=422)
    try:
        path = _db_path(request)
        store.init_db(path)
        with closing(store.open_connection(path)) as conn:
            rows = store.list_entries(conn, protocol, search_text=search,
                                      provider=provider, topic=topic)
    except Exception as exc:
        _log_failure("list_entries", exc)
        return JSONResponse({"error": "dorkbook unavailable"}, status_code=500)
    return JSONResponse({"entries": rows})


@dorkbook_router.get("/api/dorkbook/defaults")
async def read_defaults(request: Request, session: Session = Depends(get_session)):
    try:
        values = defaults.read_defaults(request.app.state.main_config_path)
    except Exception as exc:
        _log_failure("read_defaults", exc)
        return JSONResponse({"error": "discovery configuration unavailable"}, status_code=500)
    return JSONResponse({"defaults": values}, headers={"Cache-Control": "no-store"})


@dorkbook_router.post("/api/dorkbook/entries", status_code=201)
async def create_entry(body: DorkbookEntryCreateRequest, request: Request,
                       session: Session = Depends(get_session)):
    error = _security_error(request, session)
    if error is not None:
        return error
    try:
        path = _db_path(request)
        store.init_db(path)
        with closing(store.open_connection(path)) as conn:
            entry_id = store.create_entry(conn, body.protocol, body.nickname, body.query,
                                          body.notes, provider=body.provider, topic=body.topic)
            conn.commit()
    except DuplicateEntryError:
        return JSONResponse({"error": "query already exists"}, status_code=409)
    except ValueError:
        return JSONResponse({"error": "invalid dork"}, status_code=422)
    except Exception as exc:
        _log_failure("create_entry", exc)
        return JSONResponse({"error": "dorkbook operation failed"}, status_code=500)
    return JSONResponse({"entry_id": entry_id, "ok": True}, status_code=201)


@dorkbook_router.delete("/api/dorkbook/entries/{entry_id}")
async def delete_entry(entry_id: int, request: Request, session: Session = Depends(get_session)):
    error = _security_error(request, session)
    if error is not None:
        return error
    try:
        path = _db_path(request)
        store.init_db(path)
        with closing(store.open_connection(path)) as conn:
            row = store.get_entry(conn, entry_id)
            if row is None:
                return JSONResponse({"error": "entry not found"}, status_code=404)
            if row.get("row_kind") == "builtin":
                raise ReadOnlyEntryError("built-in")
            store.delete_entry(conn, entry_id)
            conn.commit()
    except ReadOnlyEntryError:
        return JSONResponse({"error": "built-in dorks are read-only"}, status_code=403)
    except Exception as exc:
        _log_failure("delete_entry", exc)
        return JSONResponse({"error": "dorkbook operation failed"}, status_code=500)
    return JSONResponse({"ok": True})


@dorkbook_router.post("/api/dorkbook/apply")
@dorkbook_router.post("/api/dorkbook/prefill")
async def apply_entry(body: DorkbookPrefillRequest, request: Request,
                      session: Session = Depends(get_session)):
    error = _security_error(request, session)
    if error is not None:
        return error
    try:
        path = _db_path(request)
        store.init_db(path)
        with closing(store.open_connection(path)) as conn:
            row = store.get_entry(conn, body.entry_id)
    except Exception as exc:
        _log_failure("apply store access", exc)
        return JSONResponse({"error": "dorkbook unavailable"}, status_code=500)
    if row is None:
        return JSONResponse({"error": "entry not found"}, status_code=404)
    provider = row.get("provider", "shodan")
    protocol = row.get("protocol")
    config_path = Path(request.app.state.main_config_path)
    if not config_path.is_file() and config_path != get_paths().config_file:
        return JSONResponse({"error": "config file not found"}, status_code=404)
    try:
        query = defaults.apply_default(provider, protocol, row["query"], config_path)
    except ValueError:
        return JSONResponse({"error": "invalid dork destination or query"}, status_code=422)
    except Exception as exc:
        _log_failure("apply config write", exc)
        return JSONResponse({"error": "failed to update discovery configuration"}, status_code=500)
    destination = "self_hosted" if provider == "self_hosted" else "shodan:" + protocol
    return JSONResponse({"ok": True, "provider": provider, "protocol": protocol,
                         "destination": destination, "query": query})
