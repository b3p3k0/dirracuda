"""Durable-side validation for the strict legacy Excel IPC frame."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass

from .models import FileTerminal
from .xls_contract import (
    CALAMINE_VERSION,
    FRAME_MAGIC,
    MAX_CELL_CHARS,
    MAX_CALAMINE_INT,
    MAX_CELLS,
    MAX_HEADER_BYTES,
    MAX_LOGICAL_UNITS,
    MAX_SHEETS,
    MAX_UNIT_LABEL_CHARS,
    MAX_XLS_COLUMNS,
    MAX_XLS_ROWS,
    MIN_CALAMINE_INT,
    PYTHON_CALAMINE_VERSION,
    UNIT_SEPARATOR,
)

FAILURE_DETAILS = {
    "encrypted": {"password_required"},
    "parse_error": {
        "calamine_failed", "control_character", "input_alias", "scalar_type",
        "sheet_metadata", "sheet_shape",
    },
    "parse_oom": {"memory_limit"},
    "parser_output_limit": {
        "cell_limit", "cell_text_limit", "dimension_limit",
        "semantic_unit_limit", "sheet_limit", "text_limit",
    },
    "unsupported_format": {"not_xls"},
}
CELL_LABEL = re.compile(r"sheet-([1-9][0-9]*)!([A-Z]{1,2})([1-9][0-9]*)\Z")


@dataclass(frozen=True, slots=True)
class XlsUnit:
    kind: str
    label: str
    scalar_type: str
    char_count: int


@dataclass(frozen=True, slots=True)
class DecodedXls:
    reason: str
    format_name: str | None = None
    text: str | None = None
    detail: str | None = None
    units: tuple[XlsUnit, ...] = ()
    logical_unit_count: int = 0
    sheet_count: int = 0
    worksheet_count: int = 0
    skipped_sheet_count: int = 0
    dense_cell_count: int = 0
    parser_version: str | None = None
    embedded_version: str | None = None


def decode_xls_frame(
    payload: bytes, *, max_text_bytes: int, max_text_chars: int,
) -> DecodedXls:
    """Validate one complete XLS frame without trusting the sandbox child."""
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
        "calamine_version", "dense_cell_count", "detail", "format",
        "logical_unit_count", "python_calamine_version", "sheet_count",
        "skipped_sheet_count", "status", "text_bytes", "text_chars", "units",
        "worksheet_count",
    }
    if type(header) is not dict or set(header) != required:
        return _invalid()
    if not _valid_header_scalars(header, body):
        return _invalid()
    units = _decode_units(header["units"], header["sheet_count"])
    if units is None:
        return _invalid()
    status = header["status"]
    version = header["python_calamine_version"]
    engine_version = header["calamine_version"]
    if status == "success":
        if (
            version != PYTHON_CALAMINE_VERSION
            or engine_version != CALAMINE_VERSION
            or header["detail"] is not None
            or header["text_bytes"] > max_text_bytes
            or header["text_chars"] > max_text_chars
            or len(units) != header["logical_unit_count"]
            or len(units) > header["dense_cell_count"]
            or header["worksheet_count"] + header["skipped_sheet_count"]
            != header["sheet_count"]
            or header["text_chars"]
            != sum(unit.char_count for unit in units) + max(0, len(units) - 1)
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
                or _unsafe_text(unit_text)
                or not _valid_scalar_text(unit.scalar_type, unit_text)
            ):
                return _invalid()
            cursor += unit.char_count
            if index + 1 < len(units):
                if text[cursor:cursor + 1] != UNIT_SEPARATOR:
                    return _invalid()
                cursor += 1
        if cursor != len(text):
            return _invalid()
        return DecodedXls(
            "success", "xls", text, units=units,
            logical_unit_count=len(units), sheet_count=header["sheet_count"],
            worksheet_count=header["worksheet_count"],
            skipped_sheet_count=header["skipped_sheet_count"],
            dense_cell_count=header["dense_cell_count"],
            parser_version=PYTHON_CALAMINE_VERSION,
            embedded_version=CALAMINE_VERSION,
        )
    if (
        body
        or header["text_bytes"] != 0
        or header["text_chars"] != 0
        or header["logical_unit_count"] != 0
        or header["sheet_count"] != 0
        or header["worksheet_count"] != 0
        or header["skipped_sheet_count"] != 0
        or header["dense_cell_count"] != 0
        or units
    ):
        return _invalid()
    if status == "dependency_unavailable":
        if (
            header["detail"] not in {"dependency_missing", "dependency_version"}
            or version is not None
            or engine_version is not None
        ):
            return _invalid()
        return DecodedXls(
            FileTerminal.SANDBOX_UNAVAILABLE.value, "xls",
            detail=header["detail"], parser_version=version,
            embedded_version=engine_version,
        )
    mapped = {
        "encrypted": FileTerminal.ENCRYPTED.value,
        "parse_error": FileTerminal.PARSE_ERROR.value,
        "parse_oom": FileTerminal.PARSE_OOM.value,
        "parser_output_limit": FileTerminal.PARSER_OUTPUT_LIMIT.value,
        "unsupported_format": FileTerminal.UNSUPPORTED_FORMAT.value,
    }.get(status)
    if (
        mapped is None
        or version != PYTHON_CALAMINE_VERSION
        or engine_version != CALAMINE_VERSION
        or header["detail"] not in FAILURE_DETAILS.get(status, set())
    ):
        return _invalid()
    return DecodedXls(
        mapped, "xls", detail=header["detail"],
        parser_version=PYTHON_CALAMINE_VERSION,
        embedded_version=CALAMINE_VERSION,
    )


def _valid_header_scalars(header: dict[str, object], body: bytes) -> bool:
    return not (
        header["format"] != "xls"
        or type(header["status"]) is not str
        or (header["detail"] is not None and type(header["detail"]) is not str)
        or (header["python_calamine_version"] is not None
            and type(header["python_calamine_version"]) is not str)
        or (header["calamine_version"] is not None
            and type(header["calamine_version"]) is not str)
        or type(header["logical_unit_count"]) is not int
        or not 0 <= header["logical_unit_count"] <= MAX_LOGICAL_UNITS
        or type(header["sheet_count"]) is not int
        or not 0 <= header["sheet_count"] <= MAX_SHEETS
        or type(header["worksheet_count"]) is not int
        or not 0 <= header["worksheet_count"] <= header["sheet_count"]
        or type(header["skipped_sheet_count"]) is not int
        or not 0 <= header["skipped_sheet_count"] <= header["sheet_count"]
        or type(header["dense_cell_count"]) is not int
        or not 0 <= header["dense_cell_count"] <= MAX_CELLS
        or type(header["text_bytes"]) is not int
        or type(header["text_chars"]) is not int
        or header["text_bytes"] < 0
        or header["text_chars"] < 0
        or type(header["units"]) is not list
        or len(body) != header["text_bytes"]
    )


def _decode_units(raw: object, sheet_count: object) -> tuple[XlsUnit, ...] | None:
    if type(raw) is not list or len(raw) > MAX_LOGICAL_UNITS:
        return None
    units: list[XlsUnit] = []
    previous = (0, 0, 0)
    for value in raw:
        if type(value) is not dict or set(value) != {
            "kind", "label", "scalar_type", "text_chars",
        }:
            return None
        kind = value["kind"]
        label = value["label"]
        scalar_type = value["scalar_type"]
        count = value["text_chars"]
        if (
            kind != "cell"
            or type(label) is not str
            or not 1 <= len(label) <= MAX_UNIT_LABEL_CHARS
            or type(scalar_type) is not str
            or scalar_type not in {
                "bool", "date", "datetime", "duration", "float", "int",
                "string", "time",
            }
            or type(count) is not int
            or not 1 <= count <= MAX_CELL_CHARS
        ):
            return None
        match = CELL_LABEL.fullmatch(label)
        if match is None:
            return None
        sheet = int(match.group(1))
        column = _column_number(match.group(2))
        row = int(match.group(3))
        identity = (sheet, row, column)
        if (
            type(sheet_count) is not int
            or not 1 <= sheet <= sheet_count
            or not 1 <= column <= MAX_XLS_COLUMNS
            or not 1 <= row <= MAX_XLS_ROWS
            or identity <= previous
        ):
            return None
        previous = identity
        units.append(XlsUnit(kind, label, scalar_type, count))
    return tuple(units)


def _column_number(label: str) -> int:
    number = 0
    for char in label:
        number = number * 26 + ord(char) - 64
    return number


def _unsafe_text(text: str) -> bool:
    return any(
        char == "\x00"
        or ord(char) < 32 and char not in "\t\n\r"
        or 127 <= ord(char) < 160
        for char in text
    )


def _valid_scalar_text(scalar_type: str, text: str) -> bool:
    try:
        if scalar_type == "string":
            return True
        if scalar_type == "bool":
            return text in {"TRUE", "FALSE"}
        if scalar_type == "int":
            value = int(text)
            return (
                MIN_CALAMINE_INT <= value <= MAX_CALAMINE_INT
                and str(value) == text
                and text not in {"+0", "-0"}
            )
        if scalar_type == "float":
            value = float(text)
            return math.isfinite(value) and repr(value) == text
        if scalar_type == "date" and text.startswith("date:"):
            value = dt.date.fromisoformat(text[5:])
            return type(value) is dt.date and text == f"date:{value.isoformat()}"
        if scalar_type == "time" and text.startswith("time:"):
            value = dt.time.fromisoformat(text[5:])
            return value.tzinfo is None and text == (
                f"time:{value.isoformat(timespec='microseconds')}"
            )
        if scalar_type == "datetime" and text.startswith("datetime:"):
            value = dt.datetime.fromisoformat(text[9:])
            return value.tzinfo is None and text == (
                f"datetime:{value.isoformat(timespec='microseconds')}"
            )
        if scalar_type == "duration" and text.startswith("duration_us:"):
            value = text[12:]
            return str(int(value)) == value and value not in {"+0", "-0"}
    except (OverflowError, ValueError):
        return False
    return False


def _invalid() -> DecodedXls:
    return DecodedXls(FileTerminal.PARSE_ERROR.value)


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
