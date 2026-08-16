"""Content-free runtime identity and capability gate for Analyst workers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from importlib import metadata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Final

from .extract import (
    ANTIWORD_PACKAGE_REVISION,
    ANTIWORD_VERSION,
    CALAMINE_VERSION,
    DEFUSEDXML_VERSION,
    MUPDF_VERSION,
    PYMUPDF_VERSION,
    PYTHON_CALAMINE_VERSION,
    OptionalDependencyUnavailable,
    antiword_runtime_binds,
    ooxml_runtime_binds,
    pdf_runtime_binds,
    python_runtime_binds,
    xls_runtime_binds,
    extract_document,
)
from .inventory import InventoryFile
from .sandbox import strict_preflight
from .state import RESUMABLE_RUN_STATES
from .worker_contract import WorkerRunContext


PARSER_BUNDLE_KIND: Final = "analyst-parser-bundle"
PARSER_BUNDLE_VERSION: Final = 1
DETECTOR_RULES_VERSION: Final = "analyst-detectors-v1"
MAX_PARSER_CONTRACT_BYTES: Final = 2 * 1024 * 1024

_MODULE_ROOT = Path(__file__).resolve().parent
_PARSER_FILES: Final = (
    "checkpoint.py",
    "contact_contract.py",
    "detectors.py",
    "extract.py",
    "formats.py",
    "legacy_child.py",
    "legacy_contract.py",
    "legacy_frame.py",
    "ooxml_child.py",
    "ooxml_contract.py",
    "ooxml_frame.py",
    "ollama_client.py",
    "ollama_contract.py",
    "ollama_protocol.py",
    "ollama_state.py",
    "parser_child.py",
    "pdf_child.py",
    "phase1.py",
    "phase1_state.py",
    "phase2.py",
    "phase2_contract.py",
    "phase2_state.py",
    "resource_policy.py",
    "sandbox.py",
    "source_reopen.py",
    "worker_contract.py",
    "worksheet.py",
    "xls_child.py",
    "xls_contract.py",
    "xls_frame.py",
)

CancelCheck = Callable[[], bool]


class WorkerPreflightStatus(str, Enum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    RUN_STATE = "run_state"
    PARSER_DRIFT = "parser_drift"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"


@dataclass(frozen=True, slots=True)
class ParserBundleIdentity:
    canonical_json: str = field(repr=False)
    sha256: str

    def __post_init__(self) -> None:
        if type(self.canonical_json) is not str or not self.canonical_json:
            raise ValueError("parser bundle JSON must be nonempty text")
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.sha256)
        ):
            raise ValueError("parser bundle hash must be lowercase SHA-256")
        observed = hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()
        if observed != self.sha256:
            raise ValueError("parser bundle hash does not match its bytes")


@dataclass(frozen=True, slots=True)
class WorkerPreflightResult:
    status: WorkerPreflightStatus

    def __post_init__(self) -> None:
        if type(self.status) is not WorkerPreflightStatus:
            raise ValueError("worker preflight requires a closed status")

    @property
    def ok(self) -> bool:
        return self.status is WorkerPreflightStatus.SUCCESS


def current_parser_bundle() -> ParserBundleIdentity:
    """Hash the exact parser/sandbox contract and frozen dependency versions."""
    files = {
        name: _hash_contract_file(_MODULE_ROOT / name)
        for name in _PARSER_FILES
    }
    value = {
        "dependencies": {
            "antiword": ANTIWORD_VERSION,
            "antiword_package": ANTIWORD_PACKAGE_REVISION,
            "calamine": CALAMINE_VERSION,
            "defusedxml": DEFUSEDXML_VERSION,
            "mupdf": MUPDF_VERSION,
            "pymupdf": PYMUPDF_VERSION,
            "python_calamine": PYTHON_CALAMINE_VERSION,
        },
        "files": files,
        "kind": PARSER_BUNDLE_KIND,
        "version": PARSER_BUNDLE_VERSION,
    }
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return ParserBundleIdentity(
        canonical_json=canonical,
        sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def current_parser_bundle_mapping() -> dict[str, object]:
    """Return a detached mapping suitable for future immutable run creation."""
    return json.loads(current_parser_bundle().canonical_json)


def current_detector_rules() -> tuple[str, str]:
    """Return the exact deterministic detector version and source identity."""
    return DETECTOR_RULES_VERSION, _hash_contract_file(_MODULE_ROOT / "detectors.py")


def preflight_worker(
    context: WorkerRunContext,
    cancel_check: CancelCheck,
) -> WorkerPreflightResult:
    """Verify exact parser dependencies and the live strict sandbox before claim."""
    if type(context) is not WorkerRunContext:
        raise TypeError("worker preflight requires a typed run context")
    if not callable(cancel_check):
        raise TypeError("worker preflight requires a cancellation probe")
    if _cancelled(cancel_check):
        return WorkerPreflightResult(WorkerPreflightStatus.CANCELLED)
    if context.observed_state not in RESUMABLE_RUN_STATES:
        return WorkerPreflightResult(WorkerPreflightStatus.RUN_STATE)
    try:
        bundle = current_parser_bundle()
    except (OSError, ValueError):
        return WorkerPreflightResult(WorkerPreflightStatus.PARSER_DRIFT)
    if (
        bundle.canonical_json != context.parser_bundle_json
        or bundle.sha256 != context.parser_bundle_sha256
    ):
        return WorkerPreflightResult(WorkerPreflightStatus.PARSER_DRIFT)
    try:
        detector_version, detector_sha256 = current_detector_rules()
    except (OSError, ValueError):
        return WorkerPreflightResult(WorkerPreflightStatus.PARSER_DRIFT)
    if (
        context.detector_rules_version != detector_version
        or context.detector_rules_sha256 != detector_sha256
    ):
        return WorkerPreflightResult(WorkerPreflightStatus.PARSER_DRIFT)

    runtime_checks = (
        python_runtime_binds,
        pdf_runtime_binds,
        ooxml_runtime_binds,
        antiword_runtime_binds,
        xls_runtime_binds,
    )
    try:
        for check in runtime_checks:
            if _cancelled(cancel_check):
                return WorkerPreflightResult(WorkerPreflightStatus.CANCELLED)
            bindings = check()
            if type(bindings) is not tuple or not bindings:
                return WorkerPreflightResult(
                    WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE
                )
    except (
        OSError,
        RuntimeError,
        OptionalDependencyUnavailable,
        metadata.PackageNotFoundError,
        subprocess.SubprocessError,
        ImportError,
        ValueError,
    ):
        return WorkerPreflightResult(
            WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE
        )
    try:
        pdf_probe_ok = _probe_pdf_runtime(cancel_check)
    except (OSError, RuntimeError, ValueError):
        return WorkerPreflightResult(
            WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE
        )
    if not pdf_probe_ok:
        if _cancelled(cancel_check):
            return WorkerPreflightResult(WorkerPreflightStatus.CANCELLED)
        return WorkerPreflightResult(
            WorkerPreflightStatus.DEPENDENCY_UNAVAILABLE
        )
    if _cancelled(cancel_check):
        return WorkerPreflightResult(WorkerPreflightStatus.CANCELLED)
    try:
        capability = strict_preflight(cancel_check=cancel_check)
    except (OSError, RuntimeError):
        return WorkerPreflightResult(WorkerPreflightStatus.SANDBOX_UNAVAILABLE)
    if _cancelled(cancel_check):
        return WorkerPreflightResult(WorkerPreflightStatus.CANCELLED)
    if not capability.ok:
        return WorkerPreflightResult(WorkerPreflightStatus.SANDBOX_UNAVAILABLE)
    return WorkerPreflightResult(WorkerPreflightStatus.SUCCESS)


def _hash_contract_file(path: Path) -> str:
    if path.is_symlink():
        raise OSError("parser contract is not a regular owned file")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o002
            or not 0 < before.st_size <= MAX_PARSER_CONTRACT_BYTES
        ):
            raise OSError("parser contract is not a regular owned file")
        body = bytearray()
        while len(body) <= MAX_PARSER_CONTRACT_BYTES:
            chunk = os.read(fd, min(64 * 1024, MAX_PARSER_CONTRACT_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        after = os.fstat(fd)
        identity_before = (
            before.st_dev, before.st_ino, before.st_mode, before.st_uid,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_mode, after.st_uid,
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        )
        if len(body) != before.st_size or identity_before != identity_after:
            raise OSError("parser contract changed during hashing")
        return hashlib.sha256(body).hexdigest()
    finally:
        os.close(fd)


def _probe_pdf_runtime(cancel_check: CancelCheck) -> bool:
    """Exercise the exact PDF child and assert both frozen native versions."""
    if _cancelled(cancel_check):
        return False
    message = "DIRRACUDA PUBLIC PDF PREFLIGHT"
    body = _minimal_pdf(message)
    fd, raw_path = tempfile.mkstemp(prefix="dirracuda-analyst-pdf-preflight-")
    path = Path(raw_path)
    accepted = False
    try:
        offset = 0
        while offset < len(body):
            written = os.write(fd, body[offset:])
            if written <= 0:
                raise OSError("public PDF preflight write made no progress")
            offset += written
        observed = os.fstat(fd)
        expected = InventoryFile(
            relative_path="public-preflight.pdf",
            size=observed.st_size,
            mtime_ns=observed.st_mtime_ns,
            ctime_ns=observed.st_ctime_ns,
            device=observed.st_dev,
            inode=observed.st_ino,
            mode=stat.S_IMODE(observed.st_mode),
            sha256=hashlib.sha256(body).hexdigest(),
        )
        result = extract_document(
            source_fd=fd,
            expected=expected,
            cancel_check=cancel_check,
        )
        accepted = bool(
            result.ok
            and result.format_name == "pdf"
            and result.text is not None
            and result.text.strip() == message
            and result.parser_version == PYMUPDF_VERSION
            and result.embedded_version == MUPDF_VERSION
            and result.page_char_counts == (len(result.text),)
        )
    except (OSError, RuntimeError, ValueError):
        accepted = False
    finally:
        try:
            os.close(fd)
        except OSError:
            accepted = False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            accepted = False
    return accepted


def _minimal_pdf(message: str) -> bytes:
    content = f"BT /F1 12 Tf 20 100 Td ({message}) Tj ET\n".encode("ascii")
    objects = (
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode("ascii") + b">>stream\n"
        + content + b"endstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    )
    result = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, value in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj".encode("ascii") + value + b"endobj\n")
    xref_offset = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(result)


def _cancelled(cancel_check: CancelCheck) -> bool:
    value = cancel_check()
    if type(value) is not bool:
        raise TypeError("worker cancellation probe must return bool")
    return value


__all__ = [
    "PARSER_BUNDLE_KIND",
    "PARSER_BUNDLE_VERSION",
    "DETECTOR_RULES_VERSION",
    "ParserBundleIdentity",
    "WorkerPreflightResult",
    "WorkerPreflightStatus",
    "current_parser_bundle",
    "current_parser_bundle_mapping",
    "current_detector_rules",
    "preflight_worker",
]
