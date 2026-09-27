"""
Shared discovery-dork configuration contract.

Keeps field metadata, config paths, and validation logic in one place so
scan dialogs and app configuration remain aligned.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping


DORK_DEFAULTS: Dict[str, str] = {
    "smb_dork": "smb authentication: disabled",
    "ftp_dork": 'port:21 "230 Login successful"',
    "http_dork": 'http.title:"Index of /"',
}

DORK_FIELDS: tuple[str, ...] = ("smb_dork", "ftp_dork", "http_dork")

DORK_LABELS: Dict[str, str] = {
    "smb_dork": "SMB Base Query",
    "ftp_dork": "FTP Base Query",
    "http_dork": "HTTP Base Query",
}

DORK_CONFIG_PATHS: Dict[str, tuple[str, ...]] = {
    "smb_dork": ("shodan", "query_components", "base_query"),
    "ftp_dork": ("ftp", "shodan", "query_components", "base_query"),
    "http_dork": ("http", "shodan", "query_components", "base_query"),
}


def _get_nested(data: Mapping[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    return default if current is None else current


def _set_nested(data: Dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for key in path[:-1]:
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value
    current[path[-1]] = value


def read_discovery_dorks(config_data: Mapping[str, Any] | None) -> Dict[str, str]:
    """Read discovery dork values from config data, with hard defaults."""
    source = config_data if isinstance(config_data, Mapping) else {}
    resolved: Dict[str, str] = {}
    for field in DORK_FIELDS:
        default_value = DORK_DEFAULTS[field]
        raw_value = _get_nested(source, DORK_CONFIG_PATHS[field], default_value)
        resolved[field] = str(raw_value or default_value)
    return resolved


def apply_discovery_dorks(config_data: Dict[str, Any], dork_settings: Mapping[str, Any]) -> None:
    """Write normalized discovery dork values into existing config paths."""
    for field in DORK_FIELDS:
        default_value = DORK_DEFAULTS[field]
        raw_value = dork_settings.get(field, default_value) if isinstance(dork_settings, Mapping) else default_value
        normalized = str(raw_value if raw_value is not None else "").strip() or default_value
        _set_nested(config_data, DORK_CONFIG_PATHS[field], normalized)


def validate_discovery_dork(value: str, label: str) -> Dict[str, Any]:
    """Validate one discovery dork query using App Config semantics."""
    query = str(value or "").strip()
    if not query:
        return {"valid": False, "message": f"{label} cannot be blank."}
    return {"valid": True, "message": f"{label} is set."}
