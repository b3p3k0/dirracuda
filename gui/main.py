#!/usr/bin/env python3
"""
Dirracuda - Legacy Entry Point Compatibility Shim.

This module is import-compatible only.
Runtime launch must use ``./dirracuda``.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Ensure project root is importable for direct `python gui/main.py` invocation.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from gui.utils.dirracuda_loader import get_canonical_gui_class


# Backward-compatible import surface:
# from gui.main import SMBSeekGUI
SMBSeekGUI = get_canonical_gui_class()

_DEPRECATION_MESSAGE = (
    "gui/main.py is a legacy compatibility shim and is not a supported runtime "
    "entrypoint. Launch Dirracuda with ./dirracuda."
)


def main() -> int:
    """Reject legacy runtime invocation and direct users to the canonical entrypoint."""
    print(_DEPRECATION_MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
