"""
Helpers for handling legacy/default Pry wordlist paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


LEGACY_WORDLIST_PLACEHOLDER = "conf/wordlists/rockyou.txt"


def normalize_wordlist_path(value: str, *, config_path: Optional[Path] = None) -> str:
    """
    Normalize a configured wordlist path.

    Legacy placeholder behavior:
    - Keep explicit user paths unchanged.
    - Clear only the old shipped placeholder when it does not resolve to a real file.
    """
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    if not _is_legacy_placeholder(raw_value):
        return raw_value

    if _resolves_to_existing_file(raw_value, config_path=config_path):
        return raw_value

    return ""


def _is_legacy_placeholder(path_value: str) -> bool:
    normalized = path_value.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == LEGACY_WORDLIST_PLACEHOLDER


def _resolves_to_existing_file(path_value: str, *, config_path: Optional[Path]) -> bool:
    candidate = Path(path_value).expanduser()

    candidates = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        if config_path is not None:
            cfg_parent = config_path.expanduser().parent
            candidates.append(cfg_parent / candidate)
            candidates.append(cfg_parent.parent / candidate)
        else:
            candidates.append(Path.cwd() / candidate)

    seen = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.exists() and path.is_file():
                return True
        except OSError:
            continue

    return False


__all__ = ["LEGACY_WORDLIST_PLACEHOLDER", "normalize_wordlist_path"]
