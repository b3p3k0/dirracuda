"""Compatibility checks for the legacy gui/main.py runtime entrypoint shim."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import gui.main as legacy_main
from gui.utils.dirracuda_loader import load_dirracuda_module


def test_gui_main_import_aliases_canonical_gui_class():
    module = load_dirracuda_module()
    assert legacy_main.SMBSeekGUI is module.XSMBSeekGUI


def test_gui_main_runtime_invocation_exits_nonzero_with_guidance():
    repo_root = Path(__file__).resolve().parents[2]
    legacy_entrypoint = repo_root / "gui" / "main.py"

    completed = subprocess.run(
        [sys.executable, str(legacy_entrypoint)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    stderr = completed.stderr.strip().lower()
    assert "legacy compatibility shim" in stderr
    assert "./dirracuda" in stderr
