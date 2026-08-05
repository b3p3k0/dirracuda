"""
Purity guardrail for the Analyst benchmark instrument.

Shaped after shared/tests/test_sherlock_purity.py. Two invariants:

  1. Native parser libraries are never imported into the durable harness
     process. Parser imports happen only inside the sandboxed child
     (CONTRACT.md §5 "Guardrail").
  2. No raw XML parser reaches the analysis path; hostile XML is defusedxml's
     problem, behind the pre-parse gates (CONTRACT.md §6, R3).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "scripts" / "analyst_benchmark"

# Modules that make up the durable harness process.
DURABLE_MODULES = sorted(p.name for p in PKG_DIR.glob("*.py"))

BANNED_PARSER_IMPORTS: Set[str] = {
    "fitz", "pymupdf", "pdfplumber", "xlrd", "olefile", "openpyxl",
    "python-docx", "docx", "striprtf", "pypdf", "PyPDF2",
}
BANNED_XML_IMPORTS: Set[str] = {
    "xml.etree", "xml.etree.ElementTree", "xml.dom", "xml.sax",
    "xml.parsers.expat", "lxml", "lxml.etree",
}


def _imports(path: Path) -> Iterator[Tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, node.lineno


def _string_literals(path: Path) -> Iterator[Tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def test_no_parser_library_is_imported_by_the_durable_process() -> None:
    offenders: List[str] = []
    for name in DURABLE_MODULES:
        path = PKG_DIR / name
        for mod, lineno in _imports(path):
            root = mod.split(".")[0]
            if root in BANNED_PARSER_IMPORTS or mod in BANNED_PARSER_IMPORTS:
                offenders.append(f"{name}:{lineno} imports {mod}")
    assert not offenders, (
        "native parser libraries must be imported only inside the sandboxed "
        f"child, never in the durable harness: {offenders}")


def test_no_raw_xml_parser_in_the_analysis_path() -> None:
    offenders: List[str] = []
    for name in DURABLE_MODULES:
        path = PKG_DIR / name
        for mod, lineno in _imports(path):
            if mod in BANNED_XML_IMPORTS or mod.split(".")[0] == "lxml":
                offenders.append(f"{name}:{lineno} imports {mod}")
    assert not offenders, (
        f"use defusedxml behind the pre-parse gates, not a raw parser: {offenders}")


def test_parser_imports_appear_only_as_sandbox_child_source() -> None:
    """sandbox_smoke may mention pymupdf, but only inside the probe source it
    hands to the sandboxed interpreter - never as a real import."""
    path = PKG_DIR / "sandbox_smoke.py"
    real = [f"{m}:{ln}" for m, ln in _imports(path)
            if m.split(".")[0] in BANNED_PARSER_IMPORTS]
    assert not real, f"sandbox_smoke imports a parser for real: {real}"

    mentions = [ln for text, ln in _string_literals(path) if "pymupdf" in text]
    assert mentions, (
        "sandbox_smoke should carry the parser probe as child source; if this "
        "is gone, the PyMuPDF smoke check has been lost")


def test_no_module_shells_out_with_a_shell() -> None:
    """shell=False everywhere: hostile filenames must never reach a shell.

    stages.py runs one fixed, self-authored bash script for the dependency
    probe; it takes no external input, and is still invoked with shell=False.
    """
    offenders: List[str] = []
    for name in DURABLE_MODULES:
        path = PKG_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "shell" and not (
                        isinstance(kw.value, ast.Constant)
                        and kw.value.value is False):
                    offenders.append(f"{name}:{node.lineno} shell is not False")
    assert not offenders, offenders


def test_only_report_resolves_the_user_data_tree() -> None:
    """report.py is the single owner of the 0600 sink under the user-data tree.

    Other modules may write to a tempfile-managed directory (sandbox_smoke
    stages one benign PDF that way), but none of them may resolve where the
    user's data lives.
    """
    offenders: List[str] = []
    for name in DURABLE_MODULES:
        if name == "report.py":
            continue
        tree = ast.parse((PKG_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("get_paths", "bench_root"):
                offenders.append(f"{name}:{node.lineno} calls {node.func.id}()")
            if isinstance(node, ast.ImportFrom) and \
                    node.module == "shared.path_service":
                offenders.append(f"{name}:{node.lineno} imports path_service")
    assert not offenders, (
        f"only report.py may resolve the user-data tree: {offenders}")


def test_every_module_declares_a_disposition() -> None:
    """Revision-2 review: no benchmark module may exist without a named owner
    or removal card, or it becomes known dead code."""
    missing: List[str] = []
    for name in DURABLE_MODULES:
        if name in ("__init__.py", "__main__.py"):
            continue
        text = (PKG_DIR / name).read_text(encoding="utf-8")
        head = text[:2000].upper()
        if "DISPOSITION" not in head:
            missing.append(name)
    assert not missing, f"modules without a declared disposition: {missing}"
