import argparse
import ipaddress
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import uvicorn
from experimental.webui.app import create_app
from experimental.webui.auth import CredentialError, credential_exists
from experimental.webui.config import (
    WebUIConfig,
    WebUIConfigError,
    load_config,
    normalize_config,
    security_warnings,
    validate,
)

logger = logging.getLogger(__name__)
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUPS = 3


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotating handler that creates every active log with mode 0600."""

    def _open(self):
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        fd = os.open(self.baseFilename, flags, 0o600)
        if os.name != "nt":
            os.chmod(self.baseFilename, 0o600)
        return io.open(
            fd,
            self.mode,
            encoding=self.encoding,
            errors=self.errors,
            closefd=True,
        )


def _file_log_config(path: Path) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "file": {
                "()": PrivateRotatingFileHandler,
                "filename": str(path),
                "maxBytes": _LOG_MAX_BYTES,
                "backupCount": _LOG_BACKUPS,
                "encoding": "utf-8",
                "formatter": "default",
            },
        },
        "root": {"handlers": ["file"], "level": "INFO"},
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


def runtime_config_error(cfg: WebUIConfig, bind: str) -> Optional[str]:
    """Return an error string if remote TLS is misconfigured, else None."""
    try:
        is_loopback = ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return f"invalid bind address: {bind!r}"
    if is_loopback:
        return None
    if cfg.tls.enabled:
        if not cfg.tls.cert_file or not cfg.tls.key_file:
            return "remote TLS requires tls.cert_file and tls.key_file to be set"
        if not Path(cfg.tls.cert_file).is_file():
            return f"TLS cert file not found: {cfg.tls.cert_file}"
        if not Path(cfg.tls.key_file).is_file():
            return f"TLS key file not found: {cfg.tls.key_file}"
    return None


# Retain the original private name for existing imports.
_check_remote_tls = runtime_config_error


def _startup_lines(cfg: WebUIConfig, host: str, port: int) -> list:
    has_tls = cfg.tls.enabled and cfg.tls.cert_file and cfg.tls.key_file
    scheme = "https" if has_tls else "http"
    mode = "remote" if cfg.remote_enabled else "localhost"
    lines = [f"Web UI starting: mode={mode}  url={scheme}://{host}:{port}"]
    if cfg.remote_enabled:
        lines.append(f"  allowlist: {cfg.allowed_cidrs}")
    if not cfg.tls.enabled:
        msg = (
            "WARNING: TLS disabled for remote mode (allow_insecure_remote=true)"
            if cfg.remote_enabled
            else "TLS disabled (localhost only — explicit opt-out)"
        )
        lines.append(f"  {msg}")
    elif not has_tls:
        lines.append("  NOTE: TLS cert/key not configured — serving HTTP (localhost only)")
    return lines


def run(
    host: Optional[str] = None,
    port: Optional[int] = None,
    cfg: Optional[WebUIConfig] = None,
    config_path=None,
    log_file: Optional[str] = None,
) -> None:
    if cfg is None:
        try:
            cfg = load_config(config_path)
        except WebUIConfigError as exc:
            logger.error("Web UI config error: %s", exc)
            sys.exit(1)
    else:
        try:
            cfg = normalize_config(cfg)
            validate(cfg)
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

    err = runtime_config_error(cfg, bind)
    if err:
        logger.error("Web UI startup refused: %s", err)
        sys.exit(1)
    try:
        if not credential_exists():
            logger.error(
                "Web UI startup refused: no usable credential exists; run "
                "./dirracuda-d credentials set USERNAME"
            )
            sys.exit(1)
    except CredentialError as exc:
        logger.error("Web UI startup refused: %s", exc)
        sys.exit(1)

    for line in _startup_lines(cfg, bind, bind_port):
        logger.info("%s", line)
    for warning in security_warnings(cfg):
        logger.warning("%s", warning)

    ssl_certfile = cfg.tls.cert_file or None
    ssl_keyfile = cfg.tls.key_file or None
    app = create_app(cfg=cfg, config_path=config_path)
    uvicorn_options = {
        "host": bind,
        "port": bind_port,
        "h11_max_incomplete_event_size": 16 * 1024,
        "limit_concurrency": 128,
        "backlog": 128,
        "server_header": False,
        "proxy_headers": False,
    }
    if log_file:
        log_path = Path(log_file).expanduser().resolve(strict=False)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        uvicorn_options["log_config"] = _file_log_config(log_path)
    if cfg.tls.enabled and ssl_certfile and ssl_keyfile:
        uvicorn.run(
            app,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            **uvicorn_options,
        )
    else:
        uvicorn.run(app, **uvicorn_options)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, dest="config_path")
    parser.add_argument("--log-file", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    run(
        host=args.host,
        port=args.port,
        config_path=args.config_path,
        log_file=args.log_file,
    )
