"""
Confirmation gates: prove that without a flag, nothing happens.

BENCHMARK_PROTOCOL_C0B1.md §9. These are the tests that make the gates real
rather than aspirational, so they assert on observed side effects (sockets,
writes, settings reads, imports), not on the code merely claiming to be gated.
"""
from __future__ import annotations

import builtins
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG = "scripts.analyst_benchmark"


# ---------------------------------------------------------------------------
# Import must be inert
# ---------------------------------------------------------------------------
def test_importing_the_package_touches_nothing(monkeypatch) -> None:
    opened: list = []
    connected: list = []
    real_open = builtins.open
    real_sock = socket.socket

    def spy_open(*a, **kw):
        opened.append(a[0] if a else None)
        return real_open(*a, **kw)

    class SpySocket(real_sock):                # type: ignore[misc,valid-type]
        def connect(self, addr):               # noqa: D102
            connected.append(addr)
            raise AssertionError("import must not open a socket")

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(socket, "socket", SpySocket)

    for mod in list(sys.modules):
        if mod.startswith(PKG):
            del sys.modules[mod]
    __import__(PKG)

    assert not connected
    # importing must not read the user's settings or user-data tree
    bad = [p for p in opened
           if isinstance(p, (str, Path)) and ".dirracuda" in str(p)]
    assert not bad, f"import read user data: {bad}"


def test_importing_the_package_does_not_import_optional_parsers() -> None:
    for mod in list(sys.modules):
        if mod.startswith(PKG):
            del sys.modules[mod]
    __import__(PKG)
    for optional in ("pymupdf", "fitz", "pdfplumber", "xlrd", "defusedxml"):
        assert optional not in sys.modules, (
            f"{optional} was imported at package import time")


# ---------------------------------------------------------------------------
# No-argument invocation
# ---------------------------------------------------------------------------
def _run(args, timeout=60):
    return subprocess.run([sys.executable, "-m", PKG, *args],
                          cwd=str(REPO_ROOT), capture_output=True, text=True,
                          timeout=timeout, check=False, shell=False)


def test_no_argument_invocation_does_nothing() -> None:
    """The real safety check. --self-test is a separate deliberate mode."""
    cp = _run([])
    assert cp.returncode == 2
    assert "Refusing to run" in cp.stderr
    assert cp.stdout == ""


def test_stage_a_requires_the_dependency_probe_gate() -> None:
    cp = _run(["--stage", "A"])
    assert cp.returncode == 2
    assert "--confirm-dependency-probe is required" in cp.stderr


def test_stage_b_requires_confirm_live() -> None:
    cp = _run(["--stage", "B"])
    assert cp.returncode == 2
    assert "--confirm-live is required" in cp.stderr


def test_preflight_only_requires_confirm_live() -> None:
    cp = _run(["--preflight-only"])
    assert cp.returncode == 2
    assert "--confirm-live is required" in cp.stderr


# ---------------------------------------------------------------------------
# Ungated paths must not reach the network or the user-data tree
# ---------------------------------------------------------------------------
def test_ungated_invocations_open_no_socket(monkeypatch) -> None:
    """Run every refusal path with socket creation made fatal."""
    script = (
        "import socket, sys\n"
        "def boom(*a, **k):\n"
        "    raise AssertionError('socket created without --confirm-live')\n"
        "socket.socket = boom\n"
        "socket.create_connection = boom\n"
        "from scripts.analyst_benchmark.runner import main\n"
        "codes = [main([]), main(['--stage','A']), main(['--stage','B']),\n"
        "         main(['--preflight-only'])]\n"
        "print('CODES', codes)\n"
    )
    cp = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                        capture_output=True, text=True, timeout=60,
                        check=False, shell=False)
    assert cp.returncode == 0, cp.stderr
    assert "CODES [2, 2, 2, 2]" in cp.stdout


def test_ungated_invocations_never_call_get_paths() -> None:
    """get_paths() resolves the user-data tree; it is gated by --confirm-live."""
    script = (
        "import sys\n"
        "import shared.path_service as ps\n"
        "calls = []\n"
        "orig = ps.get_paths\n"
        "ps.get_paths = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]\n"
        "from scripts.analyst_benchmark.runner import main\n"
        "main([]); main(['--stage','A']); main(['--stage','B'])\n"
        "main(['--preflight-only'])\n"
        "print('GETPATHS', len(calls))\n"
    )
    cp = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                        capture_output=True, text=True, timeout=60,
                        check=False, shell=False)
    assert cp.returncode == 0, cp.stderr
    assert "GETPATHS 0" in cp.stdout


def test_self_test_makes_no_network_call() -> None:
    script = (
        "import socket\n"
        "def boom(*a, **k):\n"
        "    raise AssertionError('--self-test must not use the network')\n"
        "socket.socket = boom\n"
        "socket.create_connection = boom\n"
        "from scripts.analyst_benchmark.runner import main\n"
        "print('RC', main(['--self-test']))\n"
    )
    cp = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                        capture_output=True, text=True, timeout=120,
                        check=False, shell=False)
    assert cp.returncode == 0, cp.stderr
    assert "RC 0" in cp.stdout


# ---------------------------------------------------------------------------
# No private code path exists in C0B-1
# ---------------------------------------------------------------------------
def test_no_private_corpus_module_ships_in_c0b1() -> None:
    """corpus.py is deliberately deferred to C0B-2: unreachable, unexercised
    code must not ship on speculation."""
    assert not (REPO_ROOT / "scripts" / "analyst_benchmark" / "corpus.py").exists()


def _code_strings(py: Path):
    """String constants that are real code, with docstrings excluded.

    Matching raw file text would flag prose and, worse, flag leakscan's own
    detection patterns - the scanner has to name what it looks for.
    """
    import ast
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value not in docstrings:
            yield node.value, node.lineno


def test_no_module_references_the_private_corpus_root() -> None:
    """leakscan.py is the one exception: a leak scanner must name the thing it
    searches for. Everywhere else, naming the private root is a defect."""
    pkg = REPO_ROOT / "scripts" / "analyst_benchmark"
    offenders = []
    for py in pkg.glob("*.py"):
        if py.name == "leakscan.py":
            continue
        for value, lineno in _code_strings(py):
            if "Documents/Extracted" in value:
                offenders.append(f"{py.name}:{lineno}")
    assert not offenders, f"private corpus root named in {offenders}"


def test_leakscan_names_the_private_root_only_as_a_detection_pattern() -> None:
    scanner = REPO_ROOT / "scripts" / "analyst_benchmark" / "leakscan.py"
    from scripts.analyst_benchmark import leakscan
    assert leakscan.GENERIC_PATTERNS["private_mount"].search(
        "/home/someone/Documents/Extracted/host"), (
        "the scanner must actually detect the private mount")
    hits = [ln for v, ln in _code_strings(scanner) if "Documents/Extracted" in v]
    assert len(hits) == 1, f"expected exactly one detection pattern, got {hits}"


def test_user_data_paths_go_through_get_paths_only() -> None:
    """No module may hand-build a ~/.dirracuda path in code."""
    pkg = REPO_ROOT / "scripts" / "analyst_benchmark"
    offenders = []
    for py in pkg.glob("*.py"):
        for value, lineno in _code_strings(py):
            if "~/.dirracuda" in value or value == "~":
                offenders.append(f"{py.name}:{lineno} -> {value!r}")
    assert not offenders, offenders
