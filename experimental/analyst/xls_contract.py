"""Shared, standard-library-only limits for legacy Excel extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Final

FRAME_MAGIC: Final = b"DIRRACUDA_ANALYST_LEGACY_XLS_V1\n"
UNIT_SEPARATOR: Final = "\f"

PYTHON_CALAMINE_VERSION: Final = "0.8.2"
CALAMINE_VERSION: Final = "0.36.0"
PYTHON_CALAMINE_INIT_SHA256: Final = (
    "bbfb1506618c5afd0355213f46b1ab2147b4b4260a95be08c52f46e5bbcd168a"
)
PYTHON_CALAMINE_EXTENSION: Final = \
    "_python_calamine.cpython-314-x86_64-linux-gnu.so"
PYTHON_CALAMINE_EXTENSION_SHA256: Final = (
    "b9c2cca174524f0ec7495c66725a839a092450fb69acc587991e3e0ec018ba85"
)
INPUT_PATH: Final = Path("/input/document")
XLS_INPUT_PATH: Final = Path("/tmp/document.xls")

MAX_HEADER_BYTES: Final = 8 * 1024 * 1024
MAX_LOGICAL_UNITS: Final = 50_000
MAX_SHEETS: Final = 256
MAX_CELLS: Final = 250_000
MAX_CELL_CHARS: Final = 32_767
MAX_XLS_ROWS: Final = 65_536
MAX_XLS_COLUMNS: Final = 256
MAX_UNIT_LABEL_CHARS: Final = 32
MIN_CALAMINE_INT: Final = -(2**63)
MAX_CALAMINE_INT: Final = 2**63 - 1
