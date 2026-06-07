"""Web UI configuration: load, validate, and save webui.json."""

import contextlib
import ipaddress
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional

from shared.path_service import get_paths

_PATHS = get_paths()
_DEFAULT_CONFIG_PATH = (_PATHS.home_root / "conf.d" / "experimental" / "webui.json").resolve(strict=False)
_LEGACY_DEFAULT_CONFIG_PATH = (_PATHS.conf_dir / "webui.json").resolve(strict=False)
_DEFAULT_PORT = 2600
_LEGACY_DEFAULT_PORT = 5480
logger = logging.getLogger(__name__)
_WEBUI_SECTION_KEY = "webui"

_TOP_LEVEL_KEYS = frozenset({
    "enabled", "bind_address", "port", "remote_enabled",
    "allowed_cidrs", "session_timeout_idle", "session_timeout_absolute", "tls", "auth",
})

_TLS_KEYS = frozenset({"enabled", "cert_file", "key_file", "allow_insecure_remote"})

_AUTH_KEYS = frozenset({
    "lockout_threshold", "lockout_window_sec",
    "lockout_base_duration_sec", "lockout_max_duration_sec",
})


class WebUIConfigError(ValueError):
    """Raised for invalid or unparseable web UI configuration."""


@dataclass
class AuthConfig:
    lockout_threshold: int = 5
    lockout_window_sec: int = 900
    lockout_base_duration_sec: int = 300
    lockout_max_duration_sec: int = 3600


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
    port: int = _DEFAULT_PORT
    remote_enabled: bool = False
    allowed_cidrs: List[str] = field(default_factory=lambda: ["127.0.0.1/32", "::1/128"])
    session_timeout_idle: int = 1800
    session_timeout_absolute: int = 28800
    tls: TLSConfig = field(default_factory=TLSConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)


def _config_path(path: Optional[Path] = None) -> Path:
    return (Path(path) if path is not None else _DEFAULT_CONFIG_PATH).expanduser().resolve(strict=False)


def _is_canonical_default_path(path: Path) -> bool:
    try:
        return Path(path).expanduser().resolve(strict=False) == _DEFAULT_CONFIG_PATH
    except Exception:
        return False


def _extract_config_payload(raw: dict, source_path: Path) -> tuple[dict, bool]:
    """
    Return (config_payload, wrapped) from a webui config file.

    wrapped=True means source file used shard wrapper {"webui": {...}}.
    wrapped=False means source file used legacy flat schema.
    """
    if _WEBUI_SECTION_KEY in raw:
        nested = raw.get(_WEBUI_SECTION_KEY)
        if not isinstance(nested, dict):
            raise WebUIConfigError(
                f"Config key '{_WEBUI_SECTION_KEY}' must contain a JSON object: {source_path}"
            )
        return nested, True
    return raw, False


def _is_loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def normalize_remote_bind_address(bind_address: str, remote_enabled: bool) -> str:
    """Promote a remote-enabled loopback bind to its IP-family wildcard."""
    if not remote_enabled:
        return bind_address
    try:
        address = ipaddress.ip_address(bind_address)
    except ValueError:
        return bind_address
    if not address.is_loopback:
        return bind_address
    return "0.0.0.0" if address.version == 4 else "::"


def normalize_remote_bind(cfg: WebUIConfig) -> WebUIConfig:
    """Return a config whose remote listener can accept non-loopback traffic."""
    bind_address = normalize_remote_bind_address(
        cfg.bind_address,
        cfg.remote_enabled,
    )
    if bind_address == cfg.bind_address:
        return cfg
    return replace(cfg, bind_address=bind_address)


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


def _parse_auth(raw_auth: dict) -> AuthConfig:
    unknown = set(raw_auth) - _AUTH_KEYS
    if unknown:
        raise WebUIConfigError(f"Unknown auth keys: {sorted(unknown)}")
    cfg = AuthConfig()
    for fname in ("lockout_threshold", "lockout_window_sec",
                  "lockout_base_duration_sec", "lockout_max_duration_sec"):
        if fname in raw_auth:
            v = raw_auth[fname]
            _check_type(v, int, f"auth.{fname}")
            setattr(cfg, fname, v)
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
    if "auth" in raw:
        v = raw["auth"]
        if not isinstance(v, dict):
            raise WebUIConfigError(f"'auth': expected dict, got {type(v).__name__}")
        cfg.auth = _parse_auth(v)
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
    if not isinstance(cfg.auth, AuthConfig):
        raise WebUIConfigError(f"'auth': expected AuthConfig, got {type(cfg.auth).__name__}")
    for fname in ("lockout_threshold", "lockout_window_sec",
                  "lockout_base_duration_sec", "lockout_max_duration_sec"):
        fval = getattr(cfg.auth, fname)
        if isinstance(fval, bool) or not isinstance(fval, int):
            raise WebUIConfigError(
                f"'auth.{fname}': expected int, got {type(fval).__name__}"
            )


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
    a = cfg.auth
    if not (3 <= a.lockout_threshold <= 20):
        raise WebUIConfigError(
            f"auth.lockout_threshold {a.lockout_threshold} out of range 3-20"
        )
    if not (60 <= a.lockout_window_sec <= 3600):
        raise WebUIConfigError(
            f"auth.lockout_window_sec {a.lockout_window_sec} out of range 60-3600"
        )
    if not (30 <= a.lockout_base_duration_sec <= 3600):
        raise WebUIConfigError(
            f"auth.lockout_base_duration_sec {a.lockout_base_duration_sec} out of range 30-3600"
        )
    if not (300 <= a.lockout_max_duration_sec <= 86400):
        raise WebUIConfigError(
            f"auth.lockout_max_duration_sec {a.lockout_max_duration_sec} out of range 300-86400"
        )
    if a.lockout_max_duration_sec < a.lockout_base_duration_sec:
        raise WebUIConfigError(
            "auth.lockout_max_duration_sec must be >= auth.lockout_base_duration_sec"
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
        "auth": {
            "lockout_threshold": cfg.auth.lockout_threshold,
            "lockout_window_sec": cfg.auth.lockout_window_sec,
            "lockout_base_duration_sec": cfg.auth.lockout_base_duration_sec,
            "lockout_max_duration_sec": cfg.auth.lockout_max_duration_sec,
        },
    }


def _load_legacy_default_if_needed(target_path: Path) -> Optional[WebUIConfig]:
    """Best-effort migration helper for legacy ~/.dirracuda/conf/webui.json."""
    if not _is_canonical_default_path(target_path):
        return None
    if target_path.exists() or not _LEGACY_DEFAULT_CONFIG_PATH.exists():
        return None

    try:
        raw = json.loads(_LEGACY_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WebUIConfigError(
            f"Failed to read legacy config from {_LEGACY_DEFAULT_CONFIG_PATH}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise WebUIConfigError(
            f"Legacy config must be a JSON object, got {type(raw).__name__}"
        )

    payload, _wrapped = _extract_config_payload(raw, _LEGACY_DEFAULT_CONFIG_PATH)
    cfg = save_config(_parse_config(payload), path=target_path)
    logger.info(
        "Migrated legacy webui config to modular shard: %s -> %s",
        _LEGACY_DEFAULT_CONFIG_PATH,
        target_path,
    )
    return cfg


def load_config(path: Optional[Path] = None) -> WebUIConfig:
    """Load, parse, and validate webui.json. Returns safe defaults if absent."""
    p = _config_path(path)
    if not p.exists():
        migrated = _load_legacy_default_if_needed(p)
        if migrated is not None:
            return migrated
        return WebUIConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WebUIConfigError(f"Failed to read config from {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WebUIConfigError(
            f"Config must be a JSON object, got {type(raw).__name__}"
        )
    payload, wrapped = _extract_config_payload(raw, p)
    parsed_cfg = _parse_config(payload)
    cfg = normalize_remote_bind(parsed_cfg)
    should_migrate_legacy_port = (
        "port" in payload
        and isinstance(payload.get("port"), int)
        and not isinstance(payload.get("port"), bool)
        and payload.get("port") == _LEGACY_DEFAULT_PORT
    )
    should_migrate_remote_bind = cfg.bind_address != parsed_cfg.bind_address
    should_wrap_into_shard = _is_canonical_default_path(p) and not wrapped
    if should_migrate_legacy_port:
        cfg = replace(cfg, port=_DEFAULT_PORT)
    validate(cfg)
    if should_migrate_legacy_port or should_migrate_remote_bind or should_wrap_into_shard:
        migrated_raw = dict(payload)
        if should_migrate_legacy_port:
            migrated_raw["port"] = _DEFAULT_PORT
        if should_migrate_remote_bind:
            migrated_raw["bind_address"] = cfg.bind_address
        try:
            if _is_canonical_default_path(p):
                _atomic_write_json({_WEBUI_SECTION_KEY: migrated_raw}, p)
            else:
                _atomic_write_json(migrated_raw, p)
            if should_migrate_remote_bind:
                logger.info(
                    "webui config migration: promoted remote bind %s->%s at %s",
                    parsed_cfg.bind_address,
                    cfg.bind_address,
                    p,
                )
        except Exception as exc:
            if should_migrate_legacy_port:
                logger.warning(
                    "webui config migration: failed to persist port %s->%s at %s: %s",
                    _LEGACY_DEFAULT_PORT,
                    _DEFAULT_PORT,
                    p,
                    exc,
                )
            if should_migrate_remote_bind:
                logger.warning(
                    "webui config migration: failed to persist remote bind %s->%s"
                    " at %s: %s",
                    parsed_cfg.bind_address,
                    cfg.bind_address,
                    p,
                    exc,
                )
            if should_wrap_into_shard and not (
                should_migrate_legacy_port or should_migrate_remote_bind
            ):
                logger.warning(
                    "webui config migration: failed to wrap config into shard schema"
                    " at %s: %s",
                    p,
                    exc,
                )
    return cfg


def save_config(cfg: WebUIConfig, path: Optional[Path] = None) -> WebUIConfig:
    """Normalize, validate, and atomically write cfg; return the effective config."""
    effective_cfg = normalize_remote_bind(cfg)
    validate(effective_cfg)
    target_path = _config_path(path)
    payload = _config_to_dict(effective_cfg)
    if _is_canonical_default_path(target_path):
        _atomic_write_json({_WEBUI_SECTION_KEY: payload}, target_path)
    else:
        _atomic_write_json(payload, target_path)
    return effective_cfg
