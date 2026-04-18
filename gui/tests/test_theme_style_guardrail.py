"""Guardrail: apply_to_widget must use valid named styles from SMBSeekTheme."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional, Set

from gui.utils.style import SMBSeekTheme


REPO_ROOT = Path(__file__).resolve().parents[2]
GUI_ROOT = REPO_ROOT / "gui"
GUI_TESTS_ROOT = GUI_ROOT / "tests"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_style_literal(call: ast.Call) -> Optional[ast.Constant]:
    """Return style literal node from apply_to_widget call, or None if absent."""
    style_node: Optional[ast.AST] = None

    # apply_to_widget(widget, "style")
    if len(call.args) >= 2:
        style_node = call.args[1]

    # apply_to_widget(widget=..., style_name="style")
    for kw in call.keywords:
        if kw.arg == "style_name":
            style_node = kw.value
            break

    if isinstance(style_node, ast.Constant) and isinstance(style_node.value, str):
        return style_node
    return None


def _find_style_violations(path: Path, allowed_styles: Set[str]) -> List[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    violations: List[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "apply_to_widget"):
            continue

        style_lit = _extract_style_literal(node)
        if style_lit is None:
            violations.append(
                f"{path}:{node.lineno} apply_to_widget style_name must be a string literal"
            )
            continue

        style_name = style_lit.value
        if style_name not in allowed_styles:
            allowed = ", ".join(sorted(allowed_styles))
            violations.append(
                f"{path}:{node.lineno} unknown style '{style_name}' "
                f"(allowed: {allowed})"
            )

    return violations


def test_apply_to_widget_uses_known_styles() -> None:
    # Style keys come from the runtime theme definition (single source of truth).
    allowed_styles = set(SMBSeekTheme(use_dark_mode=False).styles.keys())
    violations: List[str] = []

    for path in sorted(GUI_ROOT.rglob("*.py")):
        if _is_under(path, GUI_TESTS_ROOT):
            continue
        violations.extend(_find_style_violations(path, allowed_styles))

    assert not violations, "Invalid apply_to_widget style usage found:\n" + "\n".join(violations)

