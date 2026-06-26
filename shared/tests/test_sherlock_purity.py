"""Purity guardrail: the Sherlock matcher must not couple to storage, network,
filesystem, or protocol layers (R1/R11)."""

import ast
import os

from shared.sherlock import (
    Severity,
    SherlockPathEntry,
    SherlockPattern,
    SherlockSettings,
    match_entries,
)

_PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sherlock")

# Module names that imply DB / network / filesystem / protocol coupling.
_FORBIDDEN_PREFIXES = (
    "sqlite3",
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "smbprotocol",
    "smbclient",
    "impacket",
    "ftplib",
    "pathlib",
    "shutil",
    "subprocess",
    "shared.database",
    "shared.smb_adapter",
    "shared.ftp_workflow",
    "shared.http_workflow",
    "shared.workflow",
    "shared.quarantine",
    "shared.clamav_scanner",
    "shared.path_service",
    "shared.config",
)

# Bare names that must never be called (filesystem access in the matcher).
_FORBIDDEN_CALLS = {"open"}


def _module_files():
    return [
        os.path.join(_PACKAGE_DIR, name)
        for name in os.listdir(_PACKAGE_DIR)
        if name.endswith(".py")
    ]


def _imported_names(tree):
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def test_no_forbidden_imports():
    for path in _module_files():
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for name in _imported_names(tree):
            for prefix in _FORBIDDEN_PREFIXES:
                assert not (name == prefix or name.startswith(prefix + ".")), (
                    "{0} imports forbidden module {1}".format(path, name)
                )


def test_no_filesystem_open_calls():
    for path in _module_files():
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in _FORBIDDEN_CALLS, (
                    "{0} calls forbidden builtin {1}".format(path, node.func.id)
                )


class _StrictEntry:
    """Path-entry stand-in exposing only the declared fields.

    Any access to a field the matcher should not read (e.g. a future
    snapshot_id / server_id / file handle) raises, so this guards against
    matcher drift toward storage coupling.
    """

    __slots__ = ("display_path", "segments", "container")

    def __init__(self, display_path, segments, container):
        self.display_path = display_path
        self.segments = segments
        self.container = container

    def __getattr__(self, name):
        raise AssertionError("matcher touched unexpected entry field: {0}".format(name))


def test_matcher_only_touches_declared_fields():
    # Drive the matcher with a strict entry; __getattr__ fires if the matcher
    # ever reaches beyond display_path/segments/container.
    entry = _StrictEntry(
        display_path="share/secret.txt",
        segments=("share", "secret.txt"),
        container="share",
    )
    pattern = SherlockPattern(
        key="k", category="c", label="l", pattern="secret", severity=Severity.HIGH
    )
    result = match_entries([entry], SherlockSettings(patterns=[pattern]))
    assert result.hit_count == 1
    assert result.highest_severity is Severity.HIGH


def test_strict_entry_guards_extra_field_access():
    # Sanity-check the guard itself: accessing an undeclared field raises.
    entry = _StrictEntry("p", ("p",), None)
    try:
        entry.snapshot_id
    except AssertionError:
        pass
    else:  # pragma: no cover
        raise AssertionError("strict entry did not guard attribute access")
