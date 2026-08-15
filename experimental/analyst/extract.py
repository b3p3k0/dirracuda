"""Durable-side routing and strict IPC validation for sandboxed text extraction."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Final

from .formats import DocumentFormat, SNIFF_BYTES, sniff_document_format
from .inventory import InventoryFile
from .legacy_contract import (
    ANTIWORD_PACKAGE_REVISION,
    ANTIWORD_VERSION,
    FRAME_MAGIC as LEGACY_FRAME_MAGIC,
    MAX_HEADER_BYTES as MAX_LEGACY_HEADER_BYTES,
)
from .legacy_frame import LegacyUnit, decode_legacy_frame
from .models import FileTerminal
from .ooxml_contract import (
    DEFUSEDXML_VERSION, FRAME_MAGIC as OOXML_FRAME_MAGIC,
    MAX_HEADER_BYTES as MAX_OOXML_HEADER_BYTES,
)
from .ooxml_frame import OoxmlUnit, decode_ooxml_frame
from .sandbox import PRLIMIT, RuntimeBind, SandboxLimits, run_sandboxed

MAX_SOURCE_BYTES: Final = 100 * 1024 * 1024
MAX_TEXT_BYTES: Final = 8 * 1024 * 1024
MAX_TEXT_CHARS: Final = 8_000_000
MAX_PDF_PAGES: Final = 10_000
FRAME_MAGIC: Final = b"DIRRACUDA_ANALYST_TEXT_V1\n"
MAX_HEADER_BYTES: Final = 4096
PDF_FRAME_MAGIC: Final = b"DIRRACUDA_ANALYST_PDF_V1\n"
MAX_PDF_HEADER_BYTES: Final = 128 * 1024
PDF_PAGE_SEPARATOR: Final = "\f"
PYMUPDF_VERSION: Final = "1.28.0"
MUPDF_VERSION: Final = "1.28.0"
CHILD_PATH: Final = Path(__file__).with_name("parser_child.py")
CHILD_DESTINATION: Final = Path("/runtime/analyst_parser_child.py")
PDF_CHILD_PATH: Final = Path(__file__).with_name("pdf_child.py")
PDF_CHILD_DESTINATION: Final = Path("/runtime/analyst_pdf_child.py")
PDF_SITE_DESTINATION: Final = Path("/runtime/site-packages/pymupdf")
OOXML_CHILD_PATH: Final = Path(__file__).with_name("ooxml_child.py")
OOXML_CHILD_DESTINATION: Final = Path("/runtime/analyst_ooxml_child.py")
OOXML_CONTRACT_PATH: Final = Path(__file__).with_name("ooxml_contract.py")
OOXML_CONTRACT_DESTINATION: Final = Path("/runtime/ooxml_contract.py")
OOXML_SITE_DESTINATION: Final = Path("/runtime/site-packages/defusedxml")
LEGACY_CHILD_PATH: Final = Path(__file__).with_name("legacy_child.py")
LEGACY_CHILD_DESTINATION: Final = Path("/runtime/analyst_legacy_child.py")
LEGACY_CONTRACT_PATH: Final = Path(__file__).with_name("legacy_contract.py")
LEGACY_CONTRACT_DESTINATION: Final = Path("/runtime/legacy_contract.py")
ANTIWORD_HOST_PATH: Final = Path("/usr/bin/antiword")
ANTIWORD_RUNTIME_PATH: Final = Path("/runtime/antiword")
ANTIWORD_DATA_HOST_PATH: Final = Path("/usr/share/antiword/UTF-8.txt")
ANTIWORD_DATA_RUNTIME_PATH: Final = Path("/runtime/antiword-data/UTF-8.txt")
DPKG_QUERY: Final = Path("/usr/bin/dpkg-query")
LDD: Final = Path("/usr/bin/ldd")

_CHILD_DETAILS: Final = {
    "binary_length", "control_character", "control_parameter", "control_word",
    "font_id", "group_depth", "hex_escape", "input_io", "memory_limit", "rtf_header",
    "source_limit",
    "text_decode", "text_limit", "trailing_content", "trailing_escape",
    "unbalanced_group", "unicode_fallback", "unicode_surrogate", "unicode_value",
    "unsupported_codepage", "unsupported_format",
}
_PDF_FAILURE_DETAILS: Final = {
    "encrypted": {"password_required"},
    "no_text_layer": {"no_text_layer"},
    "parse_oom": {"memory_limit"},
    "parse_error": {
        "control_character", "encryption_state", "format_mismatch", "page_count",
        "pdf_parse", "text_type",
    },
    "parser_output_limit": {"page_limit", "text_limit"},
}
_ENCODINGS: Final = {
    "rtf", "utf-8", "utf-8-bom", "utf-16-le-bom", "utf-16-be-bom",
    "utf-32-le-bom", "utf-32-be-bom", "windows-1252",
}


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    reason: str
    format_name: str | None = None
    encoding: str | None = None
    text: str | None = None
    detail: str | None = None
    page_char_counts: tuple[int, ...] = ()
    text_page_count: int = 0
    parser_version: str | None = None
    embedded_version: str | None = None
    ooxml_units: tuple[OoxmlUnit, ...] = ()
    logical_unit_count: int = 0
    primary_unit_count: int = 0
    member_count: int = 0
    expanded_bytes: int = 0
    legacy_units: tuple[LegacyUnit, ...] = ()
    package_revision: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason == "success"


class OptionalDependencyUnavailable(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def extract_document(
    *,
    source_fd: int,
    expected: InventoryFile,
    cancel_check=None,
    limits: SandboxLimits | None = None,
) -> ExtractionResult:
    """Route and extract one already-open named source through the C3 sandbox."""
    if not isinstance(expected, InventoryFile):
        return ExtractionResult(FileTerminal.SANDBOX_ERROR.value)
    if expected.size == 0:
        return ExtractionResult(FileTerminal.EMPTY.value)
    if expected.size < 0 or expected.size > MAX_SOURCE_BYTES:
        return ExtractionResult(FileTerminal.OVERSIZE.value)
    try:
        head = os.pread(source_fd, min(SNIFF_BYTES, expected.size), 0)
        format_name = sniff_document_format(head)
    except (OSError, ValueError):
        return ExtractionResult(FileTerminal.SANDBOX_ERROR.value)
    if format_name is None:
        return ExtractionResult(FileTerminal.UNSUPPORTED_FORMAT.value)
    try:
        if format_name is DocumentFormat.PDF:
            runtime_binds = pdf_runtime_binds()
        elif format_name is DocumentFormat.OOXML_CONTAINER:
            runtime_binds = ooxml_runtime_binds()
        elif format_name is DocumentFormat.LEGACY_OFFICE:
            runtime_binds = antiword_runtime_binds()
        else:
            runtime_binds = python_runtime_binds()
    except metadata.PackageNotFoundError:
        return ExtractionResult(
            FileTerminal.SANDBOX_UNAVAILABLE.value, format_name.value,
            detail="dependency_missing",
        )
    except OptionalDependencyUnavailable as exc:
        return ExtractionResult(
            FileTerminal.SANDBOX_UNAVAILABLE.value, format_name.value,
            detail=exc.detail,
        )
    except (ImportError, OSError, RuntimeError, ValueError,
            subprocess.SubprocessError):
        return ExtractionResult(FileTerminal.SANDBOX_UNAVAILABLE.value)
    chosen_limits = limits or SandboxLimits(
        address_space_bytes=512 * 1024 * 1024,
        cpu_seconds=20,
        open_files=32,
        tasks=8,
        wall_seconds=30.0,
        stdout_bytes=(
            MAX_TEXT_BYTES + MAX_PDF_HEADER_BYTES + len(PDF_FRAME_MAGIC) + 1
            if format_name is DocumentFormat.PDF
            else MAX_TEXT_BYTES + MAX_OOXML_HEADER_BYTES
            + len(OOXML_FRAME_MAGIC) + 1
            if format_name is DocumentFormat.OOXML_CONTAINER
            else MAX_TEXT_BYTES + MAX_LEGACY_HEADER_BYTES
            + len(LEGACY_FRAME_MAGIC) + 1
            if format_name is DocumentFormat.LEGACY_OFFICE
            else MAX_TEXT_BYTES + MAX_HEADER_BYTES + len(FRAME_MAGIC) + 1
        ),
        stderr_bytes=64 * 1024,
    )
    if format_name is DocumentFormat.PDF:
        command = (
            str(Path(sys.executable).resolve()), "-I", "-B",
            str(PDF_CHILD_DESTINATION), str(MAX_PDF_PAGES),
            str(MAX_TEXT_BYTES), str(MAX_TEXT_CHARS),
        )
    elif format_name is DocumentFormat.OOXML_CONTAINER:
        command = (
            str(Path(sys.executable).resolve()), "-I", "-B",
            str(OOXML_CHILD_DESTINATION), str(MAX_TEXT_BYTES),
            str(MAX_TEXT_CHARS),
        )
    elif format_name is DocumentFormat.LEGACY_OFFICE:
        command = (
            str(Path(sys.executable).resolve()), "-I", "-B",
            str(LEGACY_CHILD_DESTINATION), str(MAX_TEXT_BYTES),
            str(MAX_TEXT_CHARS),
        )
    else:
        command = (
            str(Path(sys.executable).resolve()),
            "-I", "-B", str(CHILD_DESTINATION), format_name.value,
            str(MAX_SOURCE_BYTES), str(MAX_TEXT_BYTES), str(MAX_TEXT_CHARS),
        )
    sandbox_result = run_sandboxed(
        source_fd=source_fd,
        expected=expected,
        command=command,
        runtime_binds=runtime_binds,
        limits=chosen_limits,
        cancel_check=cancel_check,
    )
    if not sandbox_result.ok:
        return ExtractionResult(sandbox_result.reason)
    if format_name is DocumentFormat.PDF:
        return _decode_pdf_frame(sandbox_result.stdout)
    if format_name is DocumentFormat.OOXML_CONTAINER:
        decoded = decode_ooxml_frame(
            sandbox_result.stdout,
            max_text_bytes=MAX_TEXT_BYTES,
            max_text_chars=MAX_TEXT_CHARS,
        )
        return ExtractionResult(
            decoded.reason, decoded.format_name,
            "utf-8" if decoded.reason == "success" else None,
            decoded.text, decoded.detail,
            parser_version=decoded.parser_version,
            ooxml_units=decoded.units,
            logical_unit_count=decoded.logical_unit_count,
            primary_unit_count=decoded.primary_unit_count,
            member_count=decoded.member_count,
            expanded_bytes=decoded.expanded_bytes,
        )
    if format_name is DocumentFormat.LEGACY_OFFICE:
        decoded = decode_legacy_frame(
            sandbox_result.stdout,
            max_text_bytes=MAX_TEXT_BYTES,
            max_text_chars=MAX_TEXT_CHARS,
        )
        return ExtractionResult(
            decoded.reason,
            decoded.format_name,
            "utf-8" if decoded.reason == "success" else None,
            decoded.text,
            decoded.detail,
            parser_version=decoded.parser_version,
            logical_unit_count=decoded.logical_unit_count,
            legacy_units=decoded.units,
            package_revision=decoded.package_revision,
        )
    return _decode_frame(sandbox_result.stdout, format_name)


def python_runtime_binds() -> tuple[RuntimeBind, ...]:
    """Return the exact system-Python runtime needed by the isolated child."""
    executable = Path(sys.executable).resolve(strict=True)
    prlimit = PRLIMIT.resolve(strict=True)
    bindings = _discover_python_runtime_binds(
        _runtime_identity(executable), _runtime_identity(prlimit)
    )
    for binding in bindings:
        _require_trusted_runtime(
            binding.source, allow_project=binding.source == CHILD_PATH
        )
    return bindings


def pdf_runtime_binds() -> tuple[RuntimeBind, ...]:
    """Return the base runtime plus one exact, independently pinned PDF package."""
    package_root = _pdf_package_root()
    package_identity = _package_tree_identity(package_root)
    child_identity = _runtime_identity(PDF_CHILD_PATH)
    bindings = _discover_pdf_runtime_binds(
        package_root, package_identity, child_identity
    )
    for binding in bindings:
        _require_trusted_runtime(
            binding.source,
            allow_project=(binding.source == PDF_CHILD_PATH
                           or binding.source == package_root),
        )
    # Re-walk after the cached lookup so in-place package changes invalidate trust.
    if _package_tree_identity(package_root) != package_identity:
        raise RuntimeError("PyMuPDF package changed during runtime discovery")
    if _runtime_identity(PDF_CHILD_PATH) != child_identity:
        raise RuntimeError("PDF child changed during runtime discovery")
    return bindings


def ooxml_runtime_binds() -> tuple[RuntimeBind, ...]:
    """Return base Python plus the exact pure-Python OOXML dependency."""
    package_root = _defusedxml_package_root()
    package_identity = _package_tree_identity(package_root)
    child_identity = _runtime_identity(OOXML_CHILD_PATH)
    contract_identity = _runtime_identity(OOXML_CONTRACT_PATH)
    bindings = _discover_ooxml_runtime_binds(
        package_root, package_identity, child_identity, contract_identity
    )
    project_sources = {OOXML_CHILD_PATH, OOXML_CONTRACT_PATH, package_root}
    for binding in bindings:
        _require_trusted_runtime(
            binding.source, allow_project=binding.source in project_sources
        )
    if _package_tree_identity(package_root) != package_identity:
        raise RuntimeError("defusedxml package changed during runtime discovery")
    if (_runtime_identity(OOXML_CHILD_PATH) != child_identity
            or _runtime_identity(OOXML_CONTRACT_PATH) != contract_identity):
        raise RuntimeError("OOXML runtime changed during discovery")
    return bindings


def antiword_runtime_binds() -> tuple[RuntimeBind, ...]:
    """Return the exact system Antiword runtime accepted for legacy DOC."""
    _verify_antiword_package()
    binary_identity = _runtime_identity(ANTIWORD_HOST_PATH)
    data_identity = _runtime_identity(ANTIWORD_DATA_HOST_PATH)
    child_identity = _runtime_identity(LEGACY_CHILD_PATH)
    contract_identity = _runtime_identity(LEGACY_CONTRACT_PATH)
    bindings = _discover_antiword_runtime_binds(
        binary_identity, data_identity, child_identity, contract_identity
    )
    project_sources = {LEGACY_CHILD_PATH, LEGACY_CONTRACT_PATH}
    for binding in bindings:
        _require_trusted_runtime(
            binding.source, allow_project=binding.source in project_sources
        )
    if (
        _runtime_identity(ANTIWORD_HOST_PATH) != binary_identity
        or _runtime_identity(ANTIWORD_DATA_HOST_PATH) != data_identity
        or _runtime_identity(LEGACY_CHILD_PATH) != child_identity
        or _runtime_identity(LEGACY_CONTRACT_PATH) != contract_identity
    ):
        raise RuntimeError("Antiword runtime changed during discovery")
    return bindings


def _pdf_package_root() -> Path:
    distribution = metadata.distribution("PyMuPDF")
    if distribution.version != PYMUPDF_VERSION:
        raise OptionalDependencyUnavailable("dependency_version")
    root = Path(distribution.locate_file("pymupdf")).resolve(strict=True)
    site_raw = sysconfig.get_path("platlib")
    if not site_raw:
        raise RuntimeError("Python platform library path is unavailable")
    expected = (Path(site_raw).resolve(strict=True) / "pymupdf").resolve(strict=True)
    if root != expected or root.name != "pymupdf":
        raise RuntimeError("PyMuPDF is installed outside the optional dependency lane")
    return root


def _defusedxml_package_root() -> Path:
    distribution = metadata.distribution("defusedxml")
    if distribution.version != DEFUSEDXML_VERSION:
        raise OptionalDependencyUnavailable("dependency_version")
    root = Path(distribution.locate_file("defusedxml")).resolve(strict=True)
    site_raw = sysconfig.get_path("purelib")
    if not site_raw:
        raise RuntimeError("Python pure library path is unavailable")
    expected = (Path(site_raw).resolve(strict=True) / "defusedxml").resolve(
        strict=True
    )
    if root != expected or root.name != "defusedxml":
        raise RuntimeError("defusedxml is installed outside the optional lane")
    return root


def _package_tree_identity(root: Path) -> tuple[tuple[object, ...], ...]:
    observed: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        item = path.lstat()
        if not (stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode)):
            raise RuntimeError("optional package contains an unsafe entry")
        if item.st_uid != os.getuid() or item.st_mode & 0o002:
            raise RuntimeError("optional package is writable or untrusted")
        observed.append((relative, item.st_dev, item.st_ino, item.st_size,
                         item.st_mtime_ns, stat.S_IFMT(item.st_mode)))
    if not observed:
        raise RuntimeError("optional package is empty")
    return tuple(observed)


@lru_cache(maxsize=2)
def _discover_pdf_runtime_binds(
    package_root: Path,
    _package_identity: tuple[tuple[object, ...], ...],
    _child_identity: tuple[int, int, int, int],
) -> tuple[RuntimeBind, ...]:
    base = [
        binding for binding in python_runtime_binds()
        if binding.destination != CHILD_DESTINATION
    ]
    _require_trusted_runtime(PDF_CHILD_PATH, allow_project=True)
    base.extend((
        RuntimeBind(PDF_CHILD_PATH, PDF_CHILD_DESTINATION),
        RuntimeBind(package_root, PDF_SITE_DESTINATION),
    ))
    native_files = tuple(sorted(
        path for path in package_root.iterdir()
        if path.is_file() and (path.suffix == ".so" or ".so." in path.name)
    ))
    if not native_files:
        raise RuntimeError("PyMuPDF native runtime is absent")
    for native in native_files:
        _require_trusted_runtime(native, allow_project=True)
        for source, destination in _ldd_dependencies(
            native, user_owned_root=package_root
        ):
            if source == package_root or package_root in source.parents:
                continue
            base.append(RuntimeBind(source, destination))
    unique: dict[str, RuntimeBind] = {}
    for binding in base:
        key = str(binding.destination)
        previous = unique.get(key)
        if previous is not None and previous.source != binding.source:
            raise RuntimeError("PDF runtime destinations conflict")
        unique[key] = binding
    return tuple(unique[key] for key in sorted(unique))


@lru_cache(maxsize=2)
def _discover_ooxml_runtime_binds(
    package_root: Path,
    _package_identity: tuple[tuple[object, ...], ...],
    _child_identity: tuple[int, int, int, int],
    _contract_identity: tuple[int, int, int, int],
) -> tuple[RuntimeBind, ...]:
    base = [
        binding for binding in python_runtime_binds()
        if binding.destination != CHILD_DESTINATION
    ]
    for source in (OOXML_CHILD_PATH, OOXML_CONTRACT_PATH):
        _require_trusted_runtime(source, allow_project=True)
    base.extend((
        RuntimeBind(OOXML_CHILD_PATH, OOXML_CHILD_DESTINATION),
        RuntimeBind(OOXML_CONTRACT_PATH, OOXML_CONTRACT_DESTINATION),
        RuntimeBind(package_root, OOXML_SITE_DESTINATION),
    ))
    unique: dict[str, RuntimeBind] = {}
    for binding in base:
        key = str(binding.destination)
        previous = unique.get(key)
        if previous is not None and previous.source != binding.source:
            raise RuntimeError("OOXML runtime destinations conflict")
        unique[key] = binding
    return tuple(unique[key] for key in sorted(unique))


@lru_cache(maxsize=2)
def _discover_antiword_runtime_binds(
    _binary_identity: tuple[int, int, int, int],
    _data_identity: tuple[int, int, int, int],
    _child_identity: tuple[int, int, int, int],
    _contract_identity: tuple[int, int, int, int],
) -> tuple[RuntimeBind, ...]:
    base = [
        binding for binding in python_runtime_binds()
        if binding.destination != CHILD_DESTINATION
    ]
    for source in (
        LEGACY_CHILD_PATH, LEGACY_CONTRACT_PATH, ANTIWORD_HOST_PATH,
        ANTIWORD_DATA_HOST_PATH,
    ):
        _require_trusted_runtime(
            source, allow_project=source in {LEGACY_CHILD_PATH, LEGACY_CONTRACT_PATH}
        )
    base.extend((
        RuntimeBind(LEGACY_CHILD_PATH, LEGACY_CHILD_DESTINATION),
        RuntimeBind(LEGACY_CONTRACT_PATH, LEGACY_CONTRACT_DESTINATION),
        RuntimeBind(ANTIWORD_HOST_PATH, ANTIWORD_RUNTIME_PATH),
        RuntimeBind(ANTIWORD_DATA_HOST_PATH, ANTIWORD_DATA_RUNTIME_PATH),
    ))
    base.extend(
        RuntimeBind(source, destination)
        for source, destination in _ldd_dependencies(ANTIWORD_HOST_PATH)
    )
    unique: dict[str, RuntimeBind] = {}
    for binding in base:
        key = str(binding.destination)
        previous = unique.get(key)
        if previous is not None and previous.source != binding.source:
            raise RuntimeError("Antiword runtime destinations conflict")
        unique[key] = binding
    return tuple(unique[key] for key in sorted(unique))


def _verify_antiword_package() -> None:
    try:
        binary = ANTIWORD_HOST_PATH.resolve(strict=True)
        mapping = ANTIWORD_DATA_HOST_PATH.resolve(strict=True)
        query = DPKG_QUERY.resolve(strict=True)
    except FileNotFoundError as exc:
        raise OptionalDependencyUnavailable("dependency_missing") from exc
    if binary != ANTIWORD_HOST_PATH or mapping != ANTIWORD_DATA_HOST_PATH:
        raise OptionalDependencyUnavailable("dependency_version")
    _require_trusted_runtime(query)
    completed = subprocess.run(
        [
            str(query), "-W",
            "-f=${db:Status-Status}\\n${Version}\\n",
            "antiword",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        shell=False,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    expected = f"installed\n{ANTIWORD_PACKAGE_REVISION}\n".encode("ascii")
    if (
        completed.returncode != 0
        or completed.stdout != expected
        or completed.stderr
    ):
        raise OptionalDependencyUnavailable("dependency_version")
    version = subprocess.run(
        [str(binary), "-h"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        shell=False,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    expected_version_line = (
        f"\tVersion: {ANTIWORD_VERSION}  (21 Oct 2005)".encode("ascii")
    )
    if (
        version.returncode != 0
        or version.stdout
        or len(version.stderr) > 64 * 1024
        or version.stderr.splitlines().count(expected_version_line) != 1
    ):
        raise OptionalDependencyUnavailable("dependency_version")


@lru_cache(maxsize=1)
def _discover_python_runtime_binds(
    _python_identity: tuple[int, int, int, int],
    _prlimit_identity: tuple[int, int, int, int],
) -> tuple[RuntimeBind, ...]:
    executable = Path(sys.executable).resolve(strict=True)
    stdlib_raw = sysconfig.get_path("stdlib")
    if not stdlib_raw:
        raise RuntimeError("Python stdlib path is unavailable")
    stdlib = Path(stdlib_raw).resolve(strict=True)
    prlimit = PRLIMIT.resolve(strict=True)
    for path in (executable, prlimit, stdlib, CHILD_PATH, LDD):
        _require_trusted_runtime(path, allow_project=path == CHILD_PATH)
    bindings = [
        RuntimeBind(executable, executable),
        RuntimeBind(prlimit, PRLIMIT),
        RuntimeBind(stdlib, stdlib),
        RuntimeBind(CHILD_PATH, CHILD_DESTINATION),
    ]
    for binary in (executable, prlimit):
        bindings.extend(
            RuntimeBind(source, destination)
            for source, destination in _ldd_dependencies(binary)
        )
    unique: dict[str, RuntimeBind] = {}
    for binding in bindings:
        key = str(binding.destination)
        previous = unique.get(key)
        if previous is not None and previous.source != binding.source:
            raise RuntimeError("Python runtime destinations conflict")
        unique[key] = binding
    return tuple(unique[key] for key in sorted(unique))


def _ldd_dependencies(
    binary: Path, *, user_owned_root: Path | None = None,
) -> tuple[tuple[Path, Path], ...]:
    completed = subprocess.run(
        [str(LDD), str(binary)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
        shell=False,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
        raise RuntimeError("Python runtime dependency discovery failed")
    dependencies = _parse_ldd(completed.stdout)
    for source, _destination in dependencies:
        _require_trusted_runtime(
            source,
            allow_project=(user_owned_root is not None
                           and (source == user_owned_root
                                or user_owned_root in source.parents)),
        )
    return dependencies


def _parse_ldd(output: bytes) -> tuple[tuple[Path, Path], ...]:
    try:
        lines = output.decode("ascii", errors="strict").splitlines()
    except UnicodeError as exc:
        raise RuntimeError("ldd output is not ASCII") from exc
    found: list[tuple[Path, Path]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("linux-vdso"):
            continue
        if "=>" in stripped:
            _name, raw_target = stripped.split("=>", 1)
            rendered = raw_target.strip().split(" ", 1)[0]
        else:
            rendered = stripped.split(" ", 1)[0]
        destination = Path(rendered)
        if not destination.is_absolute() or not destination.exists():
            raise RuntimeError("ldd returned an unsafe dependency")
        found.append((destination.resolve(strict=True), destination))
    if not found:
        raise RuntimeError("ldd returned no runtime dependencies")
    return tuple(found)


def _require_trusted_runtime(path: Path, *, allow_project: bool = False) -> None:
    observed = path.stat()
    if not (stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode)):
        raise RuntimeError("runtime dependency is not regular")
    expected_uid = os.getuid() if allow_project else 0
    writable_mask = 0o002 if allow_project else 0o022
    if observed.st_uid != expected_uid or observed.st_mode & writable_mask:
        raise RuntimeError("runtime dependency is writable or untrusted")


def _runtime_identity(path: Path) -> tuple[int, int, int, int]:
    observed = path.stat()
    return (observed.st_dev, observed.st_ino, observed.st_size,
            observed.st_mtime_ns)


def _decode_frame(payload: bytes, expected_format: DocumentFormat) -> ExtractionResult:
    if not payload.startswith(FRAME_MAGIC):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    rest = payload[len(FRAME_MAGIC):]
    separator = rest.find(b"\n")
    if separator < 0 or separator > MAX_HEADER_BYTES:
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    try:
        header = json.loads(
            rest[:separator].decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    body = rest[separator + 1:]
    required = {"detail", "encoding", "format", "status", "text_bytes", "text_chars"}
    if type(header) is not dict or set(header) != required:
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    if (type(header["format"]) is not str
            or type(header["status"]) is not str
            or (header["encoding"] is not None
                and type(header["encoding"]) is not str)
            or (header["detail"] is not None and type(header["detail"]) is not str)
            or header["format"] != expected_format.value
            or type(header["text_bytes"]) is not int
            or type(header["text_chars"]) is not int
            or header["text_bytes"] < 0 or header["text_chars"] < 0
            or len(body) != header["text_bytes"]):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    status = header["status"]
    if status == "success":
        if (header["detail"] is not None or header["encoding"] not in _ENCODINGS
                or header["text_bytes"] > MAX_TEXT_BYTES
                or header["text_chars"] > MAX_TEXT_CHARS):
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeError:
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        if len(text) != header["text_chars"] or _has_unsafe_control(
            text, allow_page_break=True
        ):
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        return ExtractionResult(
            "success", expected_format.value, header["encoding"], text, None
        )
    mapped = {
        "oversize": FileTerminal.OVERSIZE.value,
        "parse_oom": FileTerminal.PARSE_OOM.value,
        "parse_error": FileTerminal.PARSE_ERROR.value,
        "parser_output_limit": FileTerminal.PARSER_OUTPUT_LIMIT.value,
    }.get(status)
    if (mapped is None or body or header["encoding"] is not None
            or header["text_bytes"] != 0 or header["text_chars"] != 0
            or header["detail"] not in _CHILD_DETAILS):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    return ExtractionResult(mapped, expected_format.value, detail=header["detail"])


def _decode_pdf_frame(payload: bytes) -> ExtractionResult:
    if not payload.startswith(PDF_FRAME_MAGIC):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    rest = payload[len(PDF_FRAME_MAGIC):]
    separator = rest.find(b"\n")
    if separator < 0 or separator > MAX_PDF_HEADER_BYTES:
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    try:
        header = json.loads(
            rest[:separator].decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    body = rest[separator + 1:]
    required = {
        "detail", "format", "mupdf_version", "page_char_counts", "page_count",
        "pymupdf_version", "status", "text_bytes", "text_chars",
        "text_page_count",
    }
    if type(header) is not dict or set(header) != required:
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    counts = header["page_char_counts"]
    if (
        header["format"] != "pdf"
        or type(header["status"]) is not str
        or (header["detail"] is not None and type(header["detail"]) is not str)
        or (header["pymupdf_version"] is not None
            and type(header["pymupdf_version"]) is not str)
        or (header["mupdf_version"] is not None
            and type(header["mupdf_version"]) is not str)
        or type(header["page_count"]) is not int
        or not 0 <= header["page_count"] <= MAX_PDF_PAGES
        or type(header["text_page_count"]) is not int
        or not 0 <= header["text_page_count"] <= header["page_count"]
        or type(header["text_bytes"]) is not int
        or type(header["text_chars"]) is not int
        or header["text_bytes"] < 0 or header["text_chars"] < 0
        or type(counts) is not list
        or len(counts) != header["page_count"]
        or any(type(count) is not int or count < 0 or count > MAX_TEXT_CHARS
               for count in counts)
        or len(body) != header["text_bytes"]
    ):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    status = header["status"]
    versions_exact = (
        header["pymupdf_version"] == PYMUPDF_VERSION
        and header["mupdf_version"] == MUPDF_VERSION
    )
    page_counts = tuple(counts)
    if status == "success":
        if (
            not versions_exact or header["detail"] is not None
            or header["text_bytes"] > MAX_TEXT_BYTES
            or header["text_chars"] > MAX_TEXT_CHARS
            or header["text_page_count"] == 0
            or header["text_chars"] != sum(page_counts) + max(0, len(counts) - 1)
        ):
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeError:
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        if len(text) != header["text_chars"]:
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        cursor = 0
        nonempty = 0
        for index, count in enumerate(page_counts):
            page_text = text[cursor:cursor + count]
            if len(page_text) != count:
                return ExtractionResult(FileTerminal.PARSE_ERROR.value)
            if _has_unsafe_control(page_text, allow_page_break=False):
                return ExtractionResult(FileTerminal.PARSE_ERROR.value)
            if page_text.strip():
                nonempty += 1
            cursor += count
            if index + 1 < len(page_counts):
                if text[cursor:cursor + 1] != PDF_PAGE_SEPARATOR:
                    return ExtractionResult(FileTerminal.PARSE_ERROR.value)
                cursor += 1
        if cursor != len(text) or nonempty != header["text_page_count"]:
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        return ExtractionResult(
            "success", "pdf", "utf-8", text, None, page_counts,
            nonempty, PYMUPDF_VERSION, MUPDF_VERSION,
        )
    if body or header["text_bytes"] != 0 or header["text_chars"] != 0:
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    if status == "dependency_unavailable":
        if (header["detail"] not in {"dependency_missing", "dependency_version"}
                or page_counts or header["text_page_count"] != 0):
            return ExtractionResult(FileTerminal.PARSE_ERROR.value)
        return ExtractionResult(
            FileTerminal.SANDBOX_UNAVAILABLE.value, "pdf",
            detail=header["detail"], parser_version=header["pymupdf_version"],
            embedded_version=header["mupdf_version"],
        )
    mapped = {
        "encrypted": FileTerminal.ENCRYPTED.value,
        "no_text_layer": FileTerminal.NO_TEXT_LAYER.value,
        "parse_oom": FileTerminal.PARSE_OOM.value,
        "parse_error": FileTerminal.PARSE_ERROR.value,
        "parser_output_limit": FileTerminal.PARSER_OUTPUT_LIMIT.value,
    }.get(status)
    if (mapped is None or not versions_exact
            or header["detail"] not in _PDF_FAILURE_DETAILS.get(status, set())
            or (status != "no_text_layer" and page_counts)
            or (status == "no_text_layer" and any(page_counts))
            or header["text_page_count"] != 0):
        return ExtractionResult(FileTerminal.PARSE_ERROR.value)
    return ExtractionResult(
        mapped, "pdf", detail=header["detail"], page_char_counts=page_counts,
        parser_version=PYMUPDF_VERSION, embedded_version=MUPDF_VERSION,
    )


def _has_unsafe_control(text: str, *, allow_page_break: bool) -> bool:
    allowed = "\t\n\r\f" if allow_page_break else "\t\n\r"
    return any(
        char == "\x00"
        or ord(char) < 32 and char not in allowed
        or 127 <= ord(char) < 160
        for char in text
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate IPC key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("invalid JSON constant")
