"""Keymaster API routes for the Web UI (C34)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import experimental.keymaster.store as km_store
from experimental.keymaster.models import (
    DuplicateKeyError,
    InvalidPassphraseError,
    KeymasterLockedError,
    PassphraseRequiredError,
)
from experimental.webui.dependencies import get_session, same_origin, validate_csrf
from experimental.webui.experimental_models import (
    KeymasterApplyRequest,
    KeymasterKeyCreateRequest,
    KeymasterKeyUpdateRequest,
    KeymasterUnlockRequest,
)
from experimental.webui.sessions import Session
from shared.config import load_config as load_main_config
from shared.path_service import get_paths

logger = logging.getLogger(__name__)

keymaster_router = APIRouter()

_PATHS = get_paths()


def _km_db_path(request: Request) -> Optional[Path]:
    return getattr(request.app.state, "keymaster_db_path", None)


def _mask_key(raw: str, visible: int = 4) -> str:
    if not raw:
        return ""
    if len(raw) <= visible * 2:
        return "●" * max(len(raw), 1)
    return raw[:visible] + "…" + raw[-visible:]


def _safe_key_row(row: dict) -> dict:
    """Strip secret-adjacent internal fields; return only display-safe payload."""
    return {
        "key_id": row["key_id"],
        "provider": row["provider"],
        "label": row["label"],
        "api_key_masked": _mask_key(row.get("api_key") or ""),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_used_at": row["last_used_at"],
        "is_decrypted": row.get("is_decrypted", False),
    }


# ---------------------------------------------------------------------------
# GET /api/keymaster/status
# ---------------------------------------------------------------------------

@keymaster_router.get("/api/keymaster/status")
async def _km_status(
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            secure = km_store.secure_mode_enabled(conn)
            configured = km_store.passphrase_is_configured(conn)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("keymaster status: db error: %s", exc)
        return JSONResponse({"error": "keymaster db unavailable"}, status_code=503)

    is_unlocked = (
        not secure
        or session.keymaster_session_keys is not None
    )
    return JSONResponse({
        "secure_mode": secure,
        "passphrase_configured": configured,
        "is_unlocked": is_unlocked,
    })


# ---------------------------------------------------------------------------
# GET /api/keymaster/keys
# ---------------------------------------------------------------------------

@keymaster_router.get("/api/keymaster/keys")
async def _km_list_keys(
    request: Request,
    session: Session = Depends(get_session),
    provider: str = Query(default="SHODAN"),
    search: str = Query(default=""),
) -> JSONResponse:
    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            secure = km_store.secure_mode_enabled(conn)
            rows = km_store.list_keys(
                conn,
                provider,
                search,
                session_keys=session.keymaster_session_keys,
            )
        finally:
            conn.close()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.warning("keymaster list_keys: db error: %s", exc)
        return JSONResponse({"error": "keymaster db unavailable"}, status_code=503)

    is_unlocked = (
        not secure
        or session.keymaster_session_keys is not None
    )
    return JSONResponse({
        "keys": [_safe_key_row(r) for r in rows],
        "is_unlocked": is_unlocked,
        "secure_mode": secure,
    })


# ---------------------------------------------------------------------------
# POST /api/keymaster/unlock
# ---------------------------------------------------------------------------

@keymaster_router.post("/api/keymaster/unlock")
async def _km_unlock(
    body: KeymasterUnlockRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            session_keys = km_store.unlock_session_keys(conn, body.passphrase)
        finally:
            conn.close()
    except InvalidPassphraseError:
        return JSONResponse({"error": "invalid passphrase"}, status_code=400)
    except PassphraseRequiredError:
        return JSONResponse({"error": "passphrase not configured"}, status_code=400)
    except Exception as exc:
        logger.warning("keymaster unlock: db error: %s", exc)
        return JSONResponse({"error": "keymaster db unavailable"}, status_code=503)

    session.keymaster_session_keys = session_keys
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/keymaster/keys
# ---------------------------------------------------------------------------

@keymaster_router.post("/api/keymaster/keys", status_code=201)
async def _km_create_key(
    body: KeymasterKeyCreateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            key_id = km_store.create_key(
                conn,
                provider=body.provider,
                label=body.label,
                api_key=body.api_key,
                notes=body.notes,
                session_keys=session.keymaster_session_keys,
            )
            conn.commit()
        finally:
            conn.close()
    except KeymasterLockedError:
        return JSONResponse({"error": "keymaster locked"}, status_code=403)
    except PassphraseRequiredError:
        return JSONResponse({"error": "passphrase required"}, status_code=403)
    except DuplicateKeyError:
        return JSONResponse({"error": "duplicate key"}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.warning("keymaster create_key: error: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)

    return JSONResponse({"key_id": key_id}, status_code=201)


# ---------------------------------------------------------------------------
# PATCH /api/keymaster/keys/{key_id}
# ---------------------------------------------------------------------------

@keymaster_router.patch("/api/keymaster/keys/{key_id}")
async def _km_update_key(
    key_id: int,
    body: KeymasterKeyUpdateRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    db_path = _km_db_path(request)
    resolved_api_key = body.api_key.strip()

    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            if not resolved_api_key:
                existing = km_store.get_key(
                    conn, key_id, session_keys=session.keymaster_session_keys
                )
                if existing is None:
                    return JSONResponse({"error": "key not found"}, status_code=404)
                resolved_api_key = existing.get("api_key") or ""
                if not resolved_api_key:
                    return JSONResponse(
                        {"error": "keymaster locked — cannot resolve existing key"},
                        status_code=403,
                    )
            km_store.update_key(
                conn,
                key_id,
                label=body.label,
                api_key=resolved_api_key,
                notes=body.notes,
                session_keys=session.keymaster_session_keys,
            )
            conn.commit()
        finally:
            conn.close()
    except KeyError:
        return JSONResponse({"error": "key not found"}, status_code=404)
    except KeymasterLockedError:
        return JSONResponse({"error": "keymaster locked"}, status_code=403)
    except PassphraseRequiredError:
        return JSONResponse({"error": "passphrase required"}, status_code=403)
    except DuplicateKeyError:
        return JSONResponse({"error": "duplicate key"}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        logger.warning("keymaster update_key %d: error: %s", key_id, exc)
        return JSONResponse({"error": "internal error"}, status_code=500)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# DELETE /api/keymaster/keys/{key_id}
# ---------------------------------------------------------------------------

@keymaster_router.delete("/api/keymaster/keys/{key_id}")
async def _km_delete_key(
    key_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            deleted = km_store.delete_key(conn, key_id)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("keymaster delete_key %d: error: %s", key_id, exc)
        return JSONResponse({"error": "internal error"}, status_code=500)

    if not deleted:
        return JSONResponse({"error": "key not found"}, status_code=404)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# POST /api/keymaster/apply
# ---------------------------------------------------------------------------

@keymaster_router.post("/api/keymaster/apply")
async def _km_apply(
    body: KeymasterApplyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    if not same_origin(request):
        return JSONResponse({"error": "origin check failed"}, status_code=403)
    csrf_tok = request.headers.get("X-CSRF-Token")
    if not validate_csrf(csrf_tok, session.csrf_token):
        return JSONResponse({"error": "CSRF validation failed"}, status_code=403)

    main_config_path: Optional[Path] = request.app.state.main_config_path
    if main_config_path is None:
        return JSONResponse({"error": "config file not found"}, status_code=404)

    db_path = _km_db_path(request)
    try:
        km_store.init_db(db_path)
        conn = km_store.open_connection(db_path)
        try:
            row = km_store.get_key(
                conn, body.key_id, session_keys=session.keymaster_session_keys
            )
            if row is None:
                return JSONResponse({"error": "key not found"}, status_code=404)

            api_key = row.get("api_key") or ""
            if not api_key:
                return JSONResponse({"error": "keymaster locked"}, status_code=403)

            _write_api_key_to_config(main_config_path, api_key)

            km_store.touch_last_used(conn, body.key_id)
            conn.commit()
        finally:
            conn.close()
    except KeymasterLockedError:
        return JSONResponse({"error": "keymaster locked"}, status_code=403)
    except FileNotFoundError:
        return JSONResponse({"error": "config file not found"}, status_code=404)
    except Exception as exc:
        logger.warning("keymaster apply key_id=%d: error: %s", body.key_id, exc)
        return JSONResponse({"error": "internal error"}, status_code=500)

    return JSONResponse({"ok": True})


def _write_api_key_to_config(main_config_path: Path, api_key: str) -> None:
    """Write shodan.api_key to config, using canonical or direct-JSON branch."""
    is_canonical = (
        main_config_path.resolve(strict=False)
        == _PATHS.config_file.resolve(strict=False)
    )

    if is_canonical:
        cfg = load_main_config(str(main_config_path))
        shodan_cfg = cfg.get("shodan", default={}) or {}
        shodan_cfg["api_key"] = api_key
        if not cfg.update_sections({"shodan": shodan_cfg}):
            raise RuntimeError("failed to persist shodan section")
    else:
        if not main_config_path.is_file():
            raise FileNotFoundError(f"config file not found: {main_config_path}")
        data = json.loads(main_config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config file root must be a JSON object")
        data.setdefault("shodan", {})["api_key"] = api_key
        main_config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
