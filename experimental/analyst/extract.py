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
from pathlib import Path
from typing import Final

from .formats import SNIFF_BYTES, TextFormat, sniff_text_format
from .inventory import InventoryFile
from .models import FileTerminal
from .sandbox import PRLIMIT, RuntimeBind, SandboxLimits, run_sandboxed

MAX_SOURCE_BYTES: Final = 100 * 1024 * 1024
MAX_TEXT_BYTES: Final = 8 * 1024 * 1024
MAX_TEXT_CHARS: Final = 8_000_000
FRAME_MAGIC: Final = b"DIRRACUDA_ANALYST_TEXT_V1\n"
MAX_HEADER_BYTES: Final = 4096
CHILD_PATH: Final = Path(__file__).with_name("parser_child.py")
CHILD_DESTINATION: Final = Path("/runtime/analyst_parser_child.py")
LDD: Final = Path("/usr/bin/ldd")

_CHILD_DETAILS: Final = {
    "binary_length", "control_character", "control_parameter", "control_word",
    "font_id", "group_depth", "hex_escape", "input_io", "memory_limit", "rtf_header",
    "source_limit",
    "text_decode", "text_limit", "trailing_content", "trailing_escape",
    "unbalanced_group", "unicode_fallback", "unicode_surrogate", "unicode_value",
    "unsupported_codepage", "unsupported_format",
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

    @property
    def ok(self) -> bool:
        return self.reason == "success"


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
        format_name = sniff_text_format(head)
    except (OSError, ValueError):
        return ExtractionResult(FileTerminal.SANDBOX_ERROR.value)
    if format_name is None:
        return ExtractionResult(FileTerminal.UNSUPPORTED_FORMAT.value)
    try:
        runtime_binds = python_runtime_binds()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return ExtractionResult(FileTerminal.SANDBOX_UNAVAILABLE.value)
    chosen_limits = limits or SandboxLimits(
        address_space_bytes=512 * 1024 * 1024,
        cpu_seconds=20,
        open_files=32,
        tasks=8,
        wall_seconds=30.0,
        stdout_bytes=MAX_TEXT_BYTES + MAX_HEADER_BYTES,
        stderr_bytes=64 * 1024,
    )
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


def _ldd_dependencies(binary: Path) -> tuple[tuple[Path, Path], ...]:
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
        _require_trusted_runtime(source)
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


def _decode_frame(payload: bytes, expected_format: TextFormat) -> ExtractionResult:
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
        if len(text) != header["text_chars"]:
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
