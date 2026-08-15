"""Shared, standard-library-only limits for legacy Word extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Final

FRAME_MAGIC: Final = b"DIRRACUDA_ANALYST_LEGACY_DOC_V1\n"
UNIT_SEPARATOR: Final = "\f"

ANTIWORD_VERSION: Final = "0.37"
ANTIWORD_PACKAGE_REVISION: Final = "0.37-17"
ANTIWORD_PATH: Final = Path("/runtime/antiword")
ANTIWORD_DATA_PATH: Final = Path("/runtime/antiword-data")
INPUT_PATH: Final = Path("/input/document")

MAX_HEADER_BYTES: Final = 8 * 1024 * 1024
MAX_LOGICAL_UNITS: Final = 50_000
MAX_STDERR_BYTES: Final = 64 * 1024
MAX_UNIT_LABEL_CHARS: Final = 32
