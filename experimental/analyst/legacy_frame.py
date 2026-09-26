"""Durable-side validation for the strict legacy Word IPC frame."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .legacy_contract import (
    ANTIWORD_PACKAGE_REVISION,
    ANTIWORD_VERSION,
    FRAME_MAGIC,
    MAX_HEADER_BYTES,
    MAX_LOGICAL_UNITS,
    MAX_UNIT_LABEL_CHARS,
    UNIT_SEPARATOR,
)
from .models import FileTerminal

FAILURE_DETAILS = {
    "encrypted": {"password_required"},
    "parse_error": {
        "antiword_failed", "control_character", "text_decode",
    },
    "parse_oom": {"memory_limit"},
    "parser_output_limit": {
        "semantic_unit_limit", "stderr_limit", "text_limit",
    },
    "unsupported_format": {"not_word_binary", "unsupported_word_variant"},
}
UNIT_LABEL = re.compile(r"output-line-([1-9][0-9]*)\Z")


@dataclass(frozen=True, slots=True)
class LegacyUnit:
    """One exact, nonempty line emitted by Antiword."""

    kind: str
    label: str
    char_count: int


@dataclass(frozen=True, slots=True)
class DecodedLegacy:
    """Validated legacy Word child outcome."""

    reason: str
    format_name: str | None = None
    text: str | None = None
    detail: str | None = None
    units: tuple[LegacyUnit, ...] = ()
    logical_unit_count: int = 0
    parser_version: str | None = None
    package_revision: str | None = None


def decode_legacy_frame(
    payload: bytes, *, max_text_bytes: int, max_text_chars: int,
) -> DecodedLegacy:
    """Validate one complete frame without trusting the sandbox child."""
    if not payload.startswith(FRAME_MAGIC):
        return _invalid()
    rest = payload[len(FRAME_MAGIC):]
    separator = rest.find(b"\n")
    if separator < 0 or separator > MAX_HEADER_BYTES:
        return _invalid()
    try:
        header = json.loads(
            rest[:separator].decode("ascii", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        return _invalid()
    body = rest[separator + 1:]
    required = {
        "antiword_version", "detail", "format", "logical_unit_count",
        "package_revision", "status", "text_bytes", "text_chars", "units",
    }
    if type(header) is not dict or set(header) != required:
        return _invalid()
    if not _valid_header_scalars(header, body):
        return _invalid()
    units = _decode_units(header["units"], max_line_number=max_text_bytes + 1)
    if units is None:
        return _invalid()
    versions_exact = (
        header["antiword_version"] == ANTIWORD_VERSION
        and header["package_revision"] == ANTIWORD_PACKAGE_REVISION
    )
    status = header["status"]
    if status == "success":
        if (
            not versions_exact
            or header["detail"] is not None
            or header["text_bytes"] > max_text_bytes
            or header["text_chars"] > max_text_chars
            or len(units) != header["logical_unit_count"]
            or header["text_chars"]
            != sum(unit.char_count for unit in units)
            + max(0, len(units) - 1)
        ):
            return _invalid()
        try:
            text = body.decode("utf-8", errors="strict")
        except UnicodeError:
            return _invalid()
        if len(text) != header["text_chars"]:
            return _invalid()
        cursor = 0
        for index, unit in enumerate(units):
            unit_text = text[cursor:cursor + unit.char_count]
            if (
                len(unit_text) != unit.char_count
                or not unit_text.strip()
                or _unsafe_unit_text(unit_text)
            ):
                return _invalid()
            cursor += unit.char_count
            if index + 1 < len(units):
                if text[cursor:cursor + 1] != UNIT_SEPARATOR:
                    return _invalid()
                cursor += 1
        if cursor != len(text):
            return _invalid()
        return DecodedLegacy(
            "success", "doc", text, units=units,
            logical_unit_count=len(units), parser_version=ANTIWORD_VERSION,
            package_revision=ANTIWORD_PACKAGE_REVISION,
        )
    if (
        body
        or header["text_bytes"] != 0
        or header["text_chars"] != 0
        or header["logical_unit_count"] != 0
        or units
        or not versions_exact
    ):
        return _invalid()
    mapped = {
        "encrypted": FileTerminal.ENCRYPTED.value,
        "parse_error": FileTerminal.PARSE_ERROR.value,
        "parse_oom": FileTerminal.PARSE_OOM.value,
        "parser_output_limit": FileTerminal.PARSER_OUTPUT_LIMIT.value,
        "unsupported_format": FileTerminal.UNSUPPORTED_FORMAT.value,
    }.get(status)
    if mapped is None or header["detail"] not in FAILURE_DETAILS.get(status, set()):
        return _invalid()
    return DecodedLegacy(
        mapped, "doc", detail=header["detail"],
        parser_version=ANTIWORD_VERSION,
        package_revision=ANTIWORD_PACKAGE_REVISION,
    )


def _valid_header_scalars(header: dict[str, object], body: bytes) -> bool:
    return not (
        header["format"] != "doc"
        or type(header["status"]) is not str
        or (header["detail"] is not None and type(header["detail"]) is not str)
        or type(header["antiword_version"]) is not str
        or type(header["package_revision"]) is not str
        or type(header["logical_unit_count"]) is not int
        or not 0 <= header["logical_unit_count"] <= MAX_LOGICAL_UNITS
        or type(header["text_bytes"]) is not int
        or type(header["text_chars"]) is not int
        or header["text_bytes"] < 0
        or header["text_chars"] < 0
        or type(header["units"]) is not list
        or len(body) != header["text_bytes"]
    )


def _decode_units(
    raw: object, *, max_line_number: int,
) -> tuple[LegacyUnit, ...] | None:
    if type(raw) is not list or len(raw) > MAX_LOGICAL_UNITS:
        return None
    units: list[LegacyUnit] = []
    previous_line = 0
    for value in raw:
        if type(value) is not dict or set(value) != {"kind", "label", "text_chars"}:
            return None
        kind = value["kind"]
        label = value["label"]
        count = value["text_chars"]
        if (
            kind != "output_line"
            or type(label) is not str
            or not 1 <= len(label) <= MAX_UNIT_LABEL_CHARS
            or type(count) is not int
            or count <= 0
        ):
            return None
        match = UNIT_LABEL.fullmatch(label)
        if match is None:
            return None
        line_number = int(match.group(1))
        if line_number <= previous_line or line_number > max_line_number:
            return None
        previous_line = line_number
        units.append(LegacyUnit(kind, label, count))
    return tuple(units)


def _unsafe_unit_text(text: str) -> bool:
    return any(
        char == "\x00"
        or ord(char) < 32 and char != "\t"
        or 127 <= ord(char) < 160
        for char in text
    )


def _invalid() -> DecodedLegacy:
    return DecodedLegacy(FileTerminal.PARSE_ERROR.value)


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
