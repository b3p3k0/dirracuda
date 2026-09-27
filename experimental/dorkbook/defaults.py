"""Persist and read the four Dorkbook search destinations through config services.

Self-hosted Search imports the old desktop query once, only when its canonical
key is absent. Typing in a search form is run-local; applying a dork saves it.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Optional

from shared.config import load_config
from shared.config_store import get_config_store
from shared.discovery_dork_config import DORK_CONFIG_PATHS, read_discovery_dorks
from shared.path_service import get_paths

from .models import PROVIDER_SELF_HOSTED, PROVIDER_SHODAN, PROTOCOLS


def destination_key(provider: str, protocol: Optional[str] = None) -> str:
    """Validate the provider/protocol pair without inventing a web protocol."""
    if provider == PROVIDER_SELF_HOSTED and protocol is None:
        return PROVIDER_SELF_HOSTED
    if provider == PROVIDER_SHODAN and protocol in PROTOCOLS:
        return f"{provider}:{protocol}"
    raise ValueError("Select Shodan with SMB/FTP/HTTP, or Self-hosted Search without a protocol.")


def _canonical(config_path) -> bool:
    return config_path is None or Path(config_path).expanduser().resolve() == get_paths().config_file.resolve()


def _load(config_path):
    # Passing the canonical filename as an explicit override bypasses shards.
    # Use the normal loader for it; overrides remain supported for callers/tests.
    return load_config(None if _canonical(config_path) else str(config_path), strict=True)


def _legacy_query() -> str:
    prefs = get_config_store().load_user_prefs()
    settings = prefs.get("unified_scan_dialog", {})
    value = settings.get("searxng_query", "") if isinstance(settings, dict) else ""
    return value.strip() if isinstance(value, str) else ""


def read_defaults(config_path=None, *, legacy_query=None) -> dict[str, str]:
    """Read fresh saved queries, importing a nonblank legacy preference once.

    An explicit noncanonical config never imports another profile's settings.
    A malformed stored query fails explicitly instead of coercing arbitrary data.
    """
    cfg = _load(config_path)
    data = {section: cfg.get(section, default={}) for section in ("shodan", "ftp", "http")}
    for path in DORK_CONFIG_PATHS.values():
        node = data
        for key in path:
            if not isinstance(node, dict):
                raise ValueError("Shodan query configuration must contain objects and text.")
            if key not in node:
                break
            node = node[key]
        else:
            if not isinstance(node, str):
                raise ValueError("Shodan base_query must be text.")
    shodan = read_discovery_dorks(data)
    result = {f"shodan:{protocol}": shodan[f"{protocol.lower()}_dork"] for protocol in PROTOCOLS}
    section = cfg.get("se_dork", default={})
    if not isinstance(section, dict):
        raise ValueError("Self-hosted Search configuration must be an object.")
    if "default_query" in section:
        query = section["default_query"]
        if not isinstance(query, str):
            raise ValueError("Self-hosted Search default_query must be text.")
    else:
        query = legacy_query
        if query is None:
            query = _legacy_query() if _canonical(config_path) else ""
        if not isinstance(query, str):
            raise ValueError("Legacy Self-hosted Search query must be text.")
        query = query.strip()
        if query:
            updated = deepcopy(section)
            updated["default_query"] = query
            if not cfg.update_sections({"se_dork": updated}):
                raise OSError("Could not import the saved Self-hosted Search query.")
    result[PROVIDER_SELF_HOSTED] = query.strip()
    return result


def apply_default(provider: str, protocol: Optional[str], query: str, config_path=None) -> str:
    """Save only the selected destination; leave unrelated configuration intact."""
    destination_key(provider, protocol)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Dork query must be nonblank text.")
    query = query.strip()
    if provider == PROVIDER_SELF_HOSTED and len(query) > 500:
        raise ValueError("Self-hosted Search query must not exceed 500 characters.")
    cfg = _load(config_path)
    if provider == PROVIDER_SELF_HOSTED:
        path = ("se_dork", "default_query")
    else:
        path = DORK_CONFIG_PATHS[f"{protocol.lower()}_dork"]
    section = cfg.get(path[0], default={})
    if not isinstance(section, dict):
        raise ValueError(f"Configuration section {path[0]} must be an object.")
    updated = deepcopy(section)
    node = updated
    for key in path[1:-1]:
        if key not in node:
            node[key] = {}
        if not isinstance(node[key], dict):
            raise ValueError(f"Configuration field {key} must be an object.")
        node = node[key]
    node[path[-1]] = query
    if not cfg.update_sections({path[0]: updated}):
        raise OSError("Could not save the search default; the dork was not applied.")
    return query


def reconcile_query(current: str, baseline: str, latest: str) -> str:
    """Refresh an untouched form while preserving intentional manual input."""
    return latest if current == baseline else current
