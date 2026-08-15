"""Sandbox-only Antiword adapter with bounded, strict IPC output."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass

RUNTIME_PATH = "/runtime"
if __package__:
    from . import legacy_contract as _contract
else:
    if RUNTIME_PATH not in sys.path:
        sys.path.insert(0, RUNTIME_PATH)
    import legacy_contract as _contract  # type: ignore[import-not-found]

ANTIWORD_DATA_PATH = _contract.ANTIWORD_DATA_PATH
ANTIWORD_PACKAGE_REVISION = _contract.ANTIWORD_PACKAGE_REVISION
ANTIWORD_PATH = _contract.ANTIWORD_PATH
ANTIWORD_VERSION = _contract.ANTIWORD_VERSION
FRAME_MAGIC = _contract.FRAME_MAGIC
INPUT_PATH = _contract.INPUT_PATH
MAX_HEADER_BYTES = _contract.MAX_HEADER_BYTES
MAX_LOGICAL_UNITS = _contract.MAX_LOGICAL_UNITS
MAX_STDERR_BYTES = _contract.MAX_STDERR_BYTES
UNIT_SEPARATOR = _contract.UNIT_SEPARATOR

READ_SIZE = 64 * 1024
ENCRYPTED_DIAGNOSTIC = b"Encrypted documents are not supported"
NOT_WORD_DIAGNOSTIC = b"is not a Word Document."
UNSUPPORTED_DIAGNOSTICS = (
    b"autosave documents are not supported",
    b"fast saved documents are not supported",
)


class ChildFailure(Exception):
    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Unit:
    kind: str
    label: str
    text: str


class Output:
    def __init__(self, max_bytes: int, max_chars: int) -> None:
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.units: list[Unit] = []
        self.byte_count = 0
        self.char_count = 0

    def add(self, line_number: int, text: str) -> None:
        if not text.strip():
            return
        if len(self.units) >= MAX_LOGICAL_UNITS:
            raise ChildFailure("parser_output_limit", "semantic_unit_limit")
        separator = 1 if self.units else 0
        encoded = text.encode("utf-8", errors="strict")
        if (
            self.byte_count + separator + len(encoded) > self.max_bytes
            or self.char_count + separator + len(text) > self.max_chars
        ):
            raise ChildFailure("parser_output_limit", "text_limit")
        self.units.append(Unit("output_line", f"output-line-{line_number}", text))
        self.byte_count += separator + len(encoded)
        self.char_count += separator + len(text)

    def finish(self) -> str:
        return UNIT_SEPARATOR.join(unit.text for unit in self.units)


def _write_frame(
    status: str, *, detail: str | None = None, output: Output | None = None,
) -> None:
    text = output.finish() if output is not None else ""
    body = text.encode("utf-8", errors="strict")
    units = [
        {"kind": unit.kind, "label": unit.label, "text_chars": len(unit.text)}
        for unit in (output.units if output is not None else ())
    ]
    header = {
        "antiword_version": ANTIWORD_VERSION,
        "detail": detail,
        "format": "doc",
        "logical_unit_count": len(units),
        "package_revision": ANTIWORD_PACKAGE_REVISION,
        "status": status,
        "text_bytes": len(body),
        "text_chars": len(text),
        "units": units,
    }
    encoded = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(encoded) > MAX_HEADER_BYTES:
        if status != "success":
            raise RuntimeError("failure frame exceeded fixed header limit")
        _write_frame("parser_output_limit", detail="semantic_unit_limit")
        return
    sys.stdout.buffer.write(FRAME_MAGIC + encoded + b"\n" + body)
    sys.stdout.buffer.flush()


def _capture_antiword(max_stdout: int) -> tuple[int, bytes, bytes]:
    command = (
        str(ANTIWORD_PATH), "-t", "-w", "0", "-m", "UTF-8.txt",
        "-r", "-s", str(INPUT_PATH),
    )
    environment = {
        "ANTIWORDHOME": str(ANTIWORD_DATA_PATH),
        "HOME": "/sandbox-home",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": "/tmp",
    }
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, close_fds=True, cwd="/tmp", env=environment,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        raise ChildFailure("parse_error", "antiword_failed")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            for key, _events in selector.select():
                stream = key.fileobj
                chunk = os.read(stream.fileno(), READ_SIZE)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                name = key.data
                captured[name].extend(chunk)
                limit = max_stdout if name == "stdout" else MAX_STDERR_BYTES
                if len(captured[name]) > limit:
                    detail = "text_limit" if name == "stdout" else "stderr_limit"
                    raise ChildFailure("parser_output_limit", detail)
        return process.wait(), bytes(captured["stdout"]), bytes(captured["stderr"])
    except BaseException:
        _stop_process(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with suppress(OSError):
            process.kill()
    with suppress(OSError):
        process.wait()


def _normalize_output(raw: bytes, max_bytes: int, max_chars: int) -> Output:
    text = _decode_antiword_utf8(raw)
    if any(
        char == "\x00"
        or ord(char) < 32 and char not in "\t\n\r\f"
        or 127 <= ord(char) < 160
        for char in text
    ):
        raise ChildFailure("parse_error", "control_character")
    output = Output(max_bytes, max_chars)
    for line_number, line in _physical_lines(text):
        output.add(line_number, line)
    return output


def _decode_antiword_utf8(raw: bytes) -> str:
    """Decode UTF-8, narrowly repairing Antiword's paired CESU-8 surrogates."""
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    try:
        decoded = raw.decode("utf-8", errors="surrogatepass")
    except UnicodeDecodeError as exc:
        raise ChildFailure("parse_error", "text_decode") from exc
    repaired: list[str] = []
    index = 0
    while index < len(decoded):
        value = ord(decoded[index])
        if not 0xD800 <= value <= 0xDFFF:
            repaired.append(decoded[index])
            index += 1
            continue
        if not 0xD800 <= value <= 0xDBFF or index + 1 >= len(decoded):
            raise ChildFailure("parse_error", "text_decode")
        low = ord(decoded[index + 1])
        if not 0xDC00 <= low <= 0xDFFF:
            raise ChildFailure("parse_error", "text_decode")
        scalar = 0x10000 + ((value - 0xD800) << 10) + low - 0xDC00
        repaired.append(chr(scalar))
        index += 2
    return "".join(repaired)


def _physical_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield only Antiword's ASCII line boundaries, preserving Unicode text."""
    start = 0
    line_number = 1
    index = 0
    while index < len(text):
        char = text[index]
        if char not in "\n\r\f":
            index += 1
            continue
        yield line_number, text[start:index]
        line_number += 1
        index += 1
        if char == "\r" and index < len(text) and text[index] == "\n":
            index += 1
        start = index
    if start < len(text):
        yield line_number, text[start:]


def _failure_from_result(returncode: int, stderr: bytes) -> ChildFailure:
    if returncode < 0 or 129 <= returncode <= 192:
        return ChildFailure("parse_error", "antiword_failed")
    if ENCRYPTED_DIAGNOSTIC in stderr:
        return ChildFailure("encrypted", "password_required")
    if NOT_WORD_DIAGNOSTIC in stderr:
        return ChildFailure("unsupported_format", "not_word_binary")
    if any(message in stderr for message in UNSUPPORTED_DIAGNOSTICS):
        return ChildFailure("unsupported_format", "unsupported_word_variant")
    return ChildFailure("parse_error", "antiword_failed")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 2
    try:
        max_bytes = _positive(arguments[0])
        max_chars = _positive(arguments[1])
        returncode, stdout, stderr = _capture_antiword(max_bytes)
        if returncode != 0:
            raise _failure_from_result(returncode, stderr)
        if stderr:
            raise ChildFailure("parse_error", "antiword_failed")
        output = _normalize_output(stdout, max_bytes, max_chars)
        _write_frame("success", output=output)
    except ChildFailure as exc:
        _write_frame(exc.status, detail=exc.detail)
    except MemoryError:
        _write_frame("parse_oom", detail="memory_limit")
    except (OSError, subprocess.SubprocessError, ValueError):
        _write_frame("parse_error", detail="antiword_failed")
    return 0


def _positive(value: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError
    number = int(value)
    if number <= 0:
        raise ValueError
    return number


if __name__ == "__main__":
    raise SystemExit(main())
