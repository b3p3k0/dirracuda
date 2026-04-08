"""Guardrail: production GUI code must use gui.utils.safe_messagebox."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parents[2]
GUI_ROOT = REPO_ROOT / "gui"
GUI_TESTS_ROOT = GUI_ROOT / "tests"
SAFE_MESSAGEBOX_PATH = GUI_ROOT / "utils" / "safe_messagebox.py"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _find_violations(path: Path) -> List[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    violations: List[str] = []
    tkinter_aliases = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tkinter":
                    tkinter_aliases.add(alias.asname or "tkinter")
                if alias.name == "tkinter.messagebox":
                    violations.append(
                        f"{path}:{node.lineno} imports tkinter.messagebox directly"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "tkinter":
                for alias in node.names:
                    if alias.name == "messagebox":
                        violations.append(
                            f"{path}:{node.lineno} imports messagebox from tkinter"
                        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id in tkinter_aliases
            and func.value.attr == "messagebox"
        ):
            violations.append(
                f"{path}:{node.lineno} calls tkinter.messagebox directly"
            )

    return violations


def test_production_gui_uses_safe_messagebox_only() -> None:
    violations: List[str] = []

    for path in sorted(GUI_ROOT.rglob("*.py")):
        if _is_under(path, GUI_TESTS_ROOT):
            continue
        if path == SAFE_MESSAGEBOX_PATH:
            continue
        violations.extend(_find_violations(path))

    assert not violations, "Direct tkinter messagebox usage found:\n" + "\n".join(violations)
