"""Reject exception-derived text in direct Web UI HTTP responses."""

from __future__ import annotations

import ast
from pathlib import Path
from textwrap import indent

import pytest


_WEBUI_DIR = Path(__file__).resolve().parents[1]
_ROUTE_FILES = (_WEBUI_DIR / "app.py", *_WEBUI_DIR.glob("*_routes.py"))
_RESPONSE_CALLS = {"HTTPException", "JSONResponse"}
_SAFE_EXCEPTION_ATTRIBUTES = {"job_id", "reason_code"}


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple)):
        names = set()
        for item in target.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _is_safe_exception_attribute(
    node: ast.Attribute,
    exception_names: set[str],
) -> bool:
    return (
        node.attr in _SAFE_EXCEPTION_ATTRIBUTES
        and isinstance(node.value, ast.Name)
        and node.value.id in exception_names
    )


def _uses_exception_text(node: ast.AST, tainted: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Attribute) and _is_safe_exception_attribute(node, tainted):
        return False
    return any(_uses_exception_text(child, tainted) for child in ast.iter_child_nodes(node))


def _response_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _RESPONSE_CALLS
    return isinstance(func, ast.Attribute) and func.attr in _RESPONSE_CALLS


def _handler_findings(handler: ast.ExceptHandler) -> list[tuple[int, str]]:
    if not handler.name:
        return []

    tainted = {handler.name}
    assignments = [
        node
        for node in ast.walk(handler)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if isinstance(assignment, ast.Assign):
                value = assignment.value
                targets = assignment.targets
            elif isinstance(assignment, ast.AnnAssign):
                value = assignment.value
                targets = [assignment.target]
            else:
                value = assignment.value
                targets = [assignment.target]
            if value is None or not _uses_exception_text(value, tainted):
                continue
            for target in targets:
                for name in _assigned_names(target):
                    if name not in tainted:
                        tainted.add(name)
                        changed = True

    findings = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Return) and node.value is not None:
            if _uses_exception_text(node.value, tainted):
                findings.append((node.lineno, "return"))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            if _response_call(node.exc) and _uses_exception_text(node.exc, tainted):
                findings.append((node.lineno, "raise"))
    return findings


def _find_response_leaks(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            findings.extend(_handler_findings(node))
    return findings


@pytest.mark.parametrize(
    "response",
    [
        'return JSONResponse({"error": str(exc)})',
        'return JSONResponse({"error": repr(exc)})',
        'return JSONResponse({"error": exc.args[0]})',
        'return JSONResponse({"error": f"failed: {exc}"})',
        'return JSONResponse({"error": "failed: {}".format(exc)})',
        'return JSONResponse({"error": "failed: %s" % exc})',
        'detail = exc\nalias = detail\nreturn JSONResponse({"error": alias})',
        'raise HTTPException(status_code=500, detail=str(exc))',
    ],
)
def test_guard_detects_exception_derived_responses(response):
    source = (
        "def route():\n"
        "    try:\n"
        "        operation()\n"
        "    except Exception as exc:\n"
        f"{indent(response, '        ')}\n"
    )

    assert _find_response_leaks(source)


@pytest.mark.parametrize(
    "response",
    [
        'return JSONResponse({"error": "operation failed"})',
        (
            'logger.error("failed: exception_class=%s", type(exc).__name__)\n'
            'return JSONResponse({"error": "operation failed"})'
        ),
        (
            'message = messages.get(exc.reason_code, "policy failed")\n'
            'return JSONResponse({"error": message})'
        ),
    ],
)
def test_guard_accepts_fixed_responses(response):
    source = (
        "def route():\n"
        "    try:\n"
        "        operation()\n"
        "    except PasswordPolicyError as exc:\n"
        f"{indent(response, '        ')}\n"
    )

    assert _find_response_leaks(source) == []


def test_webui_routes_do_not_return_exception_text():
    findings = []
    for path in _ROUTE_FILES:
        for line, response_kind in _find_response_leaks(path.read_text(encoding="utf-8")):
            findings.append(f"{path.relative_to(_WEBUI_DIR)}:{line}: unsafe {response_kind}")

    assert findings == []
