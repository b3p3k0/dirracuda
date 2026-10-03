"""Static construction-site inventory. IDs survive line-number changes."""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def construction_sites():
    sites = []
    for path in [REPO / "dirracuda", *sorted((REPO / "gui").rglob("*.py"))]:
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        counts = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or getattr(node.func, "attr", getattr(node.func, "id", "")) != "Toplevel":
                continue
            names = []
            parent = node
            while parent in parents:
                parent = parents[parent]
                if isinstance(parent, (ast.FunctionDef, ast.ClassDef)):
                    names.insert(0, parent.name)
            key = f"{path.relative_to(REPO)}:{'.'.join(names)}"
            counts[key] = counts.get(key, 0) + 1
            sites.append({"id": key, "line": node.lineno, "ordinal": counts[key]})
    return sites

EXCLUSIONS = {
    "gui/components/scan_dialog.py:ScanDialog._create_dialog": "Unreachable legacy standalone SMB form: current dashboard imports show_unified_scan_dialog; its inherited controls are covered by start-scan. The legacy form also references the removed _open_query_budget_dialog.",
}
