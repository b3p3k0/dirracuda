"""File promotion and known-bad routing helpers for ClamAV post-processing.

Used by extract_runner.build_clamav_post_processor to relocate files after
scanning: clean files go to the extracted root, infected files go to the
quarantine known-bad subtree, scanner-error files stay in place.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_DATE_RE = re.compile(r"^\d{8}$")
_DEFAULT_QUARANTINE = Path.home() / ".dirracuda" / "quarantine"
_DEFAULT_EXTRACTED = Path.home() / ".dirracuda" / "extracted"
_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_COLLISION_LIMIT = 99


def _sanitize_segment(value: str, fallback: str = "host") -> str:
    """Return a safe single path segment (no slashes, no traversal).

    Keeps alphanumeric chars plus '-', '_', '.'. Strips leading/trailing
    punctuation. Falls back to *fallback* if the result is empty.
    """
    cleaned = "".join(c if c in _SAFE_CHARS else "-" for c in value)
    cleaned = cleaned.strip("-_.")
    return cleaned or fallback


@dataclass
class PromotionConfig:
    ip_address: str
    date_str: str          # "YYYYMMDD", validated/fallback applied by _build_promotion_config
    quarantine_root: Path  # validated/fallback applied by _build_promotion_config
    extracted_root: Path   # ~/.dirracuda/extracted or caller override
    known_bad_subdir: str  # sanitized single-segment label, default "known_bad"
    download_dir: Path     # actual quarantine dir files landed in (for rel_path derivation)


def resolve_promotion_dest(
    verdict: str,
    file_path: Path,
    share: str,
    cfg: PromotionConfig,
) -> Optional[Path]:
    """Return the destination Path for *file_path* based on its scan verdict.

    Returns None when verdict is "error" (file stays in quarantine).

    local_rel_path is derived as file_path.relative_to(cfg.download_dir / share)
    — from actual on-disk location, never from display metadata.

    Destinations:
      clean    → cfg.extracted_root / safe_host / date / safe_share / rel
      infected → cfg.quarantine_root / safe_subdir / safe_host / date / safe_share / rel
      error    → None
    """
    if verdict == "error":
        return None

    share_root = cfg.download_dir / share
    local_rel = file_path.relative_to(share_root)  # raises ValueError on mismatch

    safe_host = _sanitize_segment(cfg.ip_address, fallback="host")
    safe_share = _sanitize_segment(share, fallback="share")

    if verdict == "clean":
        return cfg.extracted_root / safe_host / cfg.date_str / safe_share / local_rel

    # infected
    safe_subdir = _sanitize_segment(cfg.known_bad_subdir, fallback="known_bad")
    return cfg.quarantine_root / safe_subdir / safe_host / cfg.date_str / safe_share / local_rel


def safe_move(src: Path, dest: Path) -> Path:
    """Move *src* to *dest*, creating parent directories as needed.

    If *dest* already exists, appends ``_1``, ``_2`` … ``_99`` to the stem
    until a free name is found.

    Returns the actual destination path used.
    Raises FileExistsError if all 99 collision slots are occupied.
    Raises OSError if the move itself fails.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    candidate = dest
    for n in range(1, _COLLISION_LIMIT + 1):
        if not candidate.exists():
            break
        candidate = dest.parent / f"{dest.stem}_{n}{dest.suffix}"
    else:
        raise FileExistsError(
            f"collision limit reached ({_COLLISION_LIMIT}) for destination {dest}"
        )

    shutil.move(str(src), str(candidate))
    return candidate


__all__ = ["PromotionConfig", "resolve_promotion_dest", "safe_move"]
