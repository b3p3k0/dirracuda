"""Web UI configuration: load, validate, and save webui.json."""

import contextlib
import ipaddress
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


_DEFAULT_CONFIG_PATH = Path.home() / ".dirracuda" / "conf" / "webui.json"

_TOP_LEVEL_KEYS = frozenset({
    "enabled", "bind_address", "port", "remote_enabled",
    "allowed_cidrs", "session_timeout_idle", "session_timeout_absolute", "tls",
})

_TLS_KEYS = frozenset({"enabled", "cert_file", "key_file", "allow_insecure_remote"})


class WebUIConfigError(ValueError):
    """Raised for invalid or unparseable web UI configuration."""


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
    allowed_cidrs: List[str] = field(default_factory=lambda: ["127.0.0.1/32", "::1/128"])
    session_timeout_idle: int = 1800
    session_timeout_absolute: int = 28800
    tls: TLSConfig = field(default_factory=TLSConfig)


def _config_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path is not None else _DEFAULT_CONFIG_PATH


def _is_loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _check_type(value, expected_type, field_name: str) -> None:
    if expected_type is int and isinstance(value, bool):
        raise WebUIConfigError(
            f"{field_name!r}: expected int, got bool"
        )
    if not isinstance(value, expected_type):
        raise WebUIConfigError(
            f"{field_name!r}: expected {expected_type.__name__},"
            f" got {type(value).__name__}"
        )


def _parse_tls(raw_tls: dict) -> TLSConfig:
    unknown = set(raw_tls) - _TLS_KEYS
    if unknown:
        raise WebUIConfigError(f"Unknown tls keys: {sorted(unknown)}")
    cfg = TLSConfig()
    if "enabled" in raw_tls:
        v = raw_tls["enabled"]
        _check_type(v, bool, "tls.enabled")
        cfg.enabled = v
    if "cert_file" in raw_tls:
        v = raw_tls["cert_file"]
        _check_type(v, str, "tls.cert_file")
        cfg.cert_file = v
    if "key_file" in raw_tls:
        v = raw_tls["key_file"]
        _check_type(v, str, "tls.key_file")
        cfg.key_file = v
    if "allow_insecure_remote" in raw_tls:
        v = raw_tls["allow_insecure_remote"]
        _check_type(v, bool, "tls.allow_insecure_remote")
        cfg.allow_insecure_remote = v
    return cfg


def _parse_config(raw: dict) -> WebUIConfig:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise WebUIConfigError(f"Unknown config keys: {sorted(unknown)}")
    cfg = WebUIConfig()
    if "enabled" in raw:
        v = raw["enabled"]
        _check_type(v, bool, "enabled")
        cfg.enabled = v
    if "bind_address" in raw:
        v = raw["bind_address"]
        _check_type(v, str, "bind_address")
        cfg.bind_address = v
    if "port" in raw:
        v = raw["port"]
        _check_type(v, int, "port")
        cfg.port = v
    if "remote_enabled" in raw:
        v = raw["remote_enabled"]
        _check_type(v, bool, "remote_enabled")
        cfg.remote_enabled = v
    if "allowed_cidrs" in raw:
        v = raw["allowed_cidrs"]
        if not isinstance(v, list):
            raise WebUIConfigError(
                f"'allowed_cidrs': expected list, got {type(v).__name__}"
            )
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise WebUIConfigError(
                    f"'allowed_cidrs[{i}]': expected str, got {type(item).__name__}"
                )
        cfg.allowed_cidrs = v
    if "session_timeout_idle" in raw:
        v = raw["session_timeout_idle"]
        _check_type(v, int, "session_timeout_idle")
        cfg.session_timeout_idle = v
    if "session_timeout_absolute" in raw:
        v = raw["session_timeout_absolute"]
        _check_type(v, int, "session_timeout_absolute")
        cfg.session_timeout_absolute = v
    if "tls" in raw:
        v = raw["tls"]
        if not isinstance(v, dict):
            raise WebUIConfigError(f"'tls': expected dict, got {type(v).__name__}")
        cfg.tls = _parse_tls(v)
    return cfg


def _validate_cfg_types(cfg: WebUIConfig) -> None:
    """Check that all WebUIConfig and TLSConfig fields have correct Python types."""
    for fname, fval in (("enabled", cfg.enabled), ("remote_enabled", cfg.remote_enabled)):
        if not isinstance(fval, bool):
            raise WebUIConfigError(f"'{fname}': expected bool, got {type(fval).__name__}")
    if not isinstance(cfg.bind_address, str):
        raise WebUIConfigError(
            f"'bind_address': expected str, got {type(cfg.bind_address).__name__}"
        )
    for fname, fval in (
        ("port", cfg.port),
        ("session_timeout_idle", cfg.session_timeout_idle),
        ("session_timeout_absolute", cfg.session_timeout_absolute),
    ):
        if isinstance(fval, bool) or not isinstance(fval, int):
            raise WebUIConfigError(f"'{fname}': expected int, got {type(fval).__name__}")
    if not isinstance(cfg.allowed_cidrs, list):
        raise WebUIConfigError(
            f"'allowed_cidrs': expected list, got {type(cfg.allowed_cidrs).__name__}"
        )
    for i, item in enumerate(cfg.allowed_cidrs):
        if not isinstance(item, str):
            raise WebUIConfigError(
                f"'allowed_cidrs[{i}]': expected str, got {type(item).__name__}"
            )
    if not isinstance(cfg.tls, TLSConfig):
        raise WebUIConfigError(f"'tls': expected TLSConfig, got {type(cfg.tls).__name__}")
    for fname, fval in (
        ("enabled", cfg.tls.enabled),
        ("allow_insecure_remote", cfg.tls.allow_insecure_remote),
    ):
        if not isinstance(fval, bool):
            raise WebUIConfigError(f"'tls.{fname}': expected bool, got {type(fval).__name__}")
    for fname, fval in (("cert_file", cfg.tls.cert_file), ("key_file", cfg.tls.key_file)):
        if not isinstance(fval, str):
            raise WebUIConfigError(f"'tls.{fname}': expected str, got {type(fval).__name__}")


def validate(cfg: WebUIConfig) -> None:
    """Validate a WebUIConfig. Raises WebUIConfigError on any failure."""
    _validate_cfg_types(cfg)
    if not (1 <= cfg.port <= 65535):
        raise WebUIConfigError(f"port {cfg.port} out of range 1-65535")
    try:
        ipaddress.ip_address(cfg.bind_address)
    except ValueError:
        raise WebUIConfigError(
            f"bind_address {cfg.bind_address!r} is not a valid IP address"
        )
    if not (300 <= cfg.session_timeout_idle <= 14400):
        raise WebUIConfigError(
            f"session_timeout_idle {cfg.session_timeout_idle} out of range 300-14400"
        )
    if not (3600 <= cfg.session_timeout_absolute <= 86400):
        raise WebUIConfigError(
            f"session_timeout_absolute {cfg.session_timeout_absolute}"
            " out of range 3600-86400"
        )
    for cidr in cfg.allowed_cidrs:
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            raise WebUIConfigError(
                f"allowed_cidrs entry {cidr!r} is not a valid CIDR"
            )
    if not _is_loopback(cfg.bind_address):
        if not cfg.remote_enabled:
            raise WebUIConfigError(
                "non-loopback bind_address requires remote_enabled=true"
            )
        if not cfg.allowed_cidrs:
            raise WebUIConfigError(
                "non-loopback bind_address requires at least one allowed_cidrs entry"
            )
        if not cfg.tls.enabled and not cfg.tls.allow_insecure_remote:
            raise WebUIConfigError(
                "non-loopback bind without TLS requires tls.allow_insecure_remote=true"
            )


def _atomic_write_json(data: dict, path: Path) -> None:
    """Write data as JSON to path atomically with mode 0600."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(path))
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _config_to_dict(cfg: WebUIConfig) -> dict:
    return {
        "enabled": cfg.enabled,
        "bind_address": cfg.bind_address,
        "port": cfg.port,
        "remote_enabled": cfg.remote_enabled,
        "allowed_cidrs": list(cfg.allowed_cidrs),
        "session_timeout_idle": cfg.session_timeout_idle,
        "session_timeout_absolute": cfg.session_timeout_absolute,
        "tls": {
            "enabled": cfg.tls.enabled,
            "cert_file": cfg.tls.cert_file,
            "key_file": cfg.tls.key_file,
            "allow_insecure_remote": cfg.tls.allow_insecure_remote,
        },
    }


def load_config(path: Optional[Path] = None) -> WebUIConfig:
    """Load, parse, and validate webui.json. Returns safe defaults if absent."""
    p = _config_path(path)
    if not p.exists():
        return WebUIConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WebUIConfigError(f"Failed to read config from {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WebUIConfigError(
            f"Config must be a JSON object, got {type(raw).__name__}"
        )
    cfg = _parse_config(raw)
    validate(cfg)
    return cfg


def save_config(cfg: WebUIConfig, path: Optional[Path] = None) -> None:
    """Validate and atomically write cfg to webui.json with mode 0600."""
    validate(cfg)
    _atomic_write_json(_config_to_dict(cfg), _config_path(path))
