import argparse
import ipaddress
import logging
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import uvicorn
from experimental.webui.app import create_app
from experimental.webui.config import WebUIConfig, WebUIConfigError, load_config, validate

logger = logging.getLogger(__name__)


def _check_remote_tls(cfg: WebUIConfig, bind: str) -> Optional[str]:
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
) -> None:
    if cfg is None:
        try:
            cfg = load_config(config_path)
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

    err = _check_remote_tls(cfg, bind)
    if err:
        logger.error("Web UI startup refused: %s", err)
        sys.exit(1)

    for line in _startup_lines(cfg, bind, bind_port):
        logger.info("%s", line)

    ssl_certfile = cfg.tls.cert_file or None
    ssl_keyfile = cfg.tls.key_file or None
    app = create_app(cfg=cfg, config_path=config_path)
    if cfg.tls.enabled and ssl_certfile and ssl_keyfile:
        uvicorn.run(app, host=bind, port=bind_port,
                    ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)
    else:
        uvicorn.run(app, host=bind, port=bind_port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None, dest="config_path")
    args = parser.parse_args()
    run(host=args.host, port=args.port, config_path=args.config_path)
