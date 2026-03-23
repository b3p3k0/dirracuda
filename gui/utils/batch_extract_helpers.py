"""
batch_extract_helpers.py

Pure-logic helpers for batch extract extension filter management.
No Tkinter dependency — all functions are unit-testable in isolation.

Used by:
  gui/components/batch_extract_dialog.py  (BatchExtractSettingsDialog)
  gui/components/extension_editor_dialog.py  (ExtensionEditorDialog)
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Special display token for extensionless files.
# This is the canonical definition; both dialog modules import from here.
NO_EXTENSION_TOKEN = "<no extension>"


def normalize_loaded_extensions(values: List[Any]) -> List[str]:
    """Clean extensions loaded from config (dedupe, lowercase, map blanks to token).

    Args:
        values: Raw list from JSON config (may contain non-strings or empty strings).

    Returns:
        Deduplicated, lowercased list with blank entries mapped to NO_EXTENSION_TOKEN.
    """
    cleaned: List[str] = []
    seen = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if not normalized:
            normalized = NO_EXTENSION_TOKEN
        normalized = normalized.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return cleaned


def load_extension_filters(
    config_path: Path,
    normalize: bool = False,
) -> Dict[str, List[str]]:
    """Load extension filters from config.json.

    Args:
        config_path: Path to config.json.
        normalize:   When True (BatchExtractSettingsDialog path):
                       - Runs normalize_loaded_extensions on each list.
                       - Inserts NO_EXTENSION_TOKEN at index 0 of included_extensions
                         if the token is absent from both lists.
                     When False (ExtensionEditorDialog path):
                       - Returns raw values exactly as stored in config.
                       - Token injection is left to the caller
                         (use ensure_no_extension_token separately).

    Returns:
        Dict with keys 'included_extensions' and 'excluded_extensions'.
    """
    result: Dict[str, List[str]] = {
        "included_extensions": [],
        "excluded_extensions": [],
    }

    if config_path and config_path.exists():
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            file_cfg = config_data.get("file_collection", {})
            included = file_cfg.get("included_extensions", [])
            excluded = file_cfg.get("excluded_extensions", [])
            if normalize:
                result["included_extensions"] = normalize_loaded_extensions(included)
                result["excluded_extensions"] = normalize_loaded_extensions(excluded)
            else:
                result["included_extensions"] = list(included)
                result["excluded_extensions"] = list(excluded)
        except Exception:
            pass  # Return empty defaults on any error

    if normalize:
        # Ensure the token is present in one of the lists
        included_lower = [e.lower() for e in result["included_extensions"]]
        excluded_lower = [e.lower() for e in result["excluded_extensions"]]
        if NO_EXTENSION_TOKEN not in included_lower and NO_EXTENSION_TOKEN not in excluded_lower:
            result["included_extensions"].insert(0, NO_EXTENSION_TOKEN)

    return result


def ensure_no_extension_token(
    included: List[str],
    excluded: List[str],
) -> None:
    """Guarantee the NO_EXTENSION_TOKEN exists in one of the lists.

    Mutates ``included`` in-place (inserts at index 0) if the token is absent
    from both lists.  No-op if the token is already present in either list.

    Args:
        included: Included-extensions list (mutated if token missing).
        excluded: Excluded-extensions list (checked but not mutated).
    """
    included_lower = [e.lower() for e in included]
    excluded_lower = [e.lower() for e in excluded]
    if NO_EXTENSION_TOKEN not in included_lower and NO_EXTENSION_TOKEN not in excluded_lower:
        included.insert(0, NO_EXTENSION_TOKEN)


def validate_extension(
    ext: str,
    source_list: List[str],
    other_list: List[str],
) -> Tuple[bool, str, str]:
    """Validate extension format and uniqueness across both lists.

    Args:
        ext:         Raw extension string entered by the user.
        source_list: The list currently being edited.
        other_list:  The opposite list (cross-list conflict check).

    Returns:
        Tuple of (is_valid, normalized_ext, error_message).
        On success error_message is ""; on failure normalized_ext is "".
    """
    # 1. Strip whitespace
    ext = ext.strip()

    # Special case: no-extension token
    if ext.lower() in (NO_EXTENSION_TOKEN, NO_EXTENSION_TOKEN.strip("<>"), "no extension"):
        token = NO_EXTENSION_TOKEN
        if any(e.lower() == token for e in source_list):
            return (False, "", f"Entry '{token}' already exists in this list")
        if any(e.lower() == token for e in other_list):
            return (False, "", f"Entry '{token}' exists in the other list")
        return (True, token, "")

    # 2. Check not empty
    if not ext:
        return (False, "", "Extension cannot be empty")

    # 3. Enforce leading dot
    if not ext.startswith("."):
        ext = "." + ext

    # 4. Convert to lowercase
    ext = ext.lower()

    # 5. Block dangerous characters (path separators, control chars)
    unsafe_chars = set('\\/\x00')
    if any(c in unsafe_chars or ord(c) < 32 for c in ext):
        return (False, "", "Extension cannot contain path separators or control characters")

    # 6. Check uniqueness in source list (case-insensitive)
    if any(e.lower() == ext for e in source_list):
        return (False, "", f"Extension '{ext}' already exists in this list")

    # 7. Check uniqueness in other list (case-insensitive)
    if any(e.lower() == ext for e in other_list):
        return (False, "", f"Extension '{ext}' exists in the other list")

    return (True, ext, "")


def sort_extensions(extensions: List[str]) -> List[str]:
    """Return a deduplicated, alphabetically sorted copy with NO_EXTENSION_TOKEN pinned at top.

    Also mutates ``extensions`` in-place to match the returned order.

    Args:
        extensions: List of extension strings to sort.

    Returns:
        The sorted list (same object as ``extensions`` after mutation).
    """
    has_no_ext = any(e.lower() == NO_EXTENSION_TOKEN for e in extensions)

    # Deduplicate while preserving first-seen case
    unique: List[str] = []
    seen: set = set()
    for ext in extensions:
        key = ext.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(ext)

    sorted_exts = sorted(
        [e for e in unique if e.lower() != NO_EXTENSION_TOKEN],
        key=str.lower,
    )
    if has_no_ext:
        sorted_exts.insert(0, NO_EXTENSION_TOKEN)

    # Mutate in-place so callers holding a reference see the updated order
    extensions.clear()
    extensions.extend(sorted_exts)

    return sorted_exts
