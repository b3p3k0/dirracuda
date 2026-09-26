"""Durable-side validation for the strict Analyst OOXML IPC frame."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .models import FileTerminal
from .ooxml_contract import (
    DEFUSEDXML_VERSION,
    FRAME_MAGIC,
    MAX_EXPANDED_BYTES,
    MAX_HEADER_BYTES,
    MAX_LOGICAL_UNITS,
    MAX_MEMBERS,
    MAX_SHEETS,
    MAX_SLIDES,
    MAX_UNIT_LABEL_CHARS,
    MAX_XML_ELEMENTS_PER_PART,
    UNIT_SEPARATOR,
)

SUPPORTED_FORMATS = {"docx", "xlsx", "pptx"}
FORMAT_UNIT_KINDS = {
    "docx": {"paragraph"},
    "xlsx": {"cell"},
    "pptx": {"comments", "notes", "slide"},
}
FAILURE_DETAILS = {
    "parse_error": {
        "archive_corrupt", "archive_duplicate", "archive_encrypted",
        "archive_path", "attribute_limit", "cell_reference", "content_types",
        "control_character", "formula_value", "main_relationship",
        "relationship", "shared_string", "text_type", "xml_parse",
    },
    "parser_output_limit": {
        "aggregate_ratio", "cell_limit", "expanded_limit", "member_limit",
        "member_ratio", "member_size", "semantic_unit_limit", "sheet_limit",
        "slide_limit", "text_limit", "xml_depth", "xml_element_limit",
        "xml_package_limit", "xml_size",
    },
    "parse_oom": {"memory_limit"},
    "unsupported_format": {
        "compression_method", "macro_enabled", "not_ooxml", "strict_ooxml",
    },
}


@dataclass(frozen=True, slots=True)
class OoxmlUnit:
    kind: str
    label: str
    char_count: int


@dataclass(frozen=True, slots=True)
class DecodedOoxml:
    reason: str
    format_name: str | None = None
    text: str | None = None
    detail: str | None = None
    units: tuple[OoxmlUnit, ...] = ()
    logical_unit_count: int = 0
    primary_unit_count: int = 0
    member_count: int = 0
    expanded_bytes: int = 0
    parser_version: str | None = None


def decode_ooxml_frame(
    payload: bytes, *, max_text_bytes: int, max_text_chars: int,
) -> DecodedOoxml:
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
        "defusedxml_version", "detail", "expanded_bytes", "format",
        "logical_unit_count", "member_count", "primary_unit_count", "status",
        "text_bytes", "text_chars", "units",
    }
    if type(header) is not dict or set(header) != required:
        return _invalid()
    if not _valid_header_scalars(header, body):
        return _invalid()
    format_name = header["format"]
    units = _decode_units(
        header["units"], format_name, header["primary_unit_count"]
    )
    if units is None:
        return _invalid()
    status = header["status"]
    version = header["defusedxml_version"]
    if status == "success":
        if (
            format_name not in SUPPORTED_FORMATS
            or version != DEFUSEDXML_VERSION
            or header["detail"] is not None
            or header["text_bytes"] > max_text_bytes
            or header["text_chars"] > max_text_chars
            or len(units) != header["logical_unit_count"]
            or not _valid_primary_count(
                format_name, header["primary_unit_count"]
            )
            or any(unit.char_count <= 0 for unit in units)
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
            if (len(unit_text) != unit.char_count
                    or not unit_text.strip()
                    or _unsafe_text(unit_text, allow_separator=False)):
                return _invalid()
            cursor += unit.char_count
            if index + 1 < len(units):
                if text[cursor:cursor + 1] != UNIT_SEPARATOR:
                    return _invalid()
                cursor += 1
        if cursor != len(text):
            return _invalid()
        return DecodedOoxml(
            reason="success",
            format_name=format_name,
            text=text,
            units=units,
            logical_unit_count=header["logical_unit_count"],
            primary_unit_count=header["primary_unit_count"],
            member_count=header["member_count"],
            expanded_bytes=header["expanded_bytes"],
            parser_version=DEFUSEDXML_VERSION,
        )
    if body or header["text_bytes"] != 0 or header["text_chars"] != 0 or units:
        return _invalid()
    if status == "dependency_unavailable":
        if (
            format_name != "ooxml"
            or header["detail"] not in {"dependency_missing", "dependency_version"}
            or header["logical_unit_count"] != 0
            or header["primary_unit_count"] != 0
            or header["member_count"] != 0
            or header["expanded_bytes"] != 0
        ):
            return _invalid()
        return DecodedOoxml(
            FileTerminal.SANDBOX_UNAVAILABLE.value,
            "ooxml",
            detail=header["detail"],
            parser_version=version,
        )
    mapped = {
        "parse_error": FileTerminal.PARSE_ERROR.value,
        "parse_oom": FileTerminal.PARSE_OOM.value,
        "parser_output_limit": FileTerminal.PARSER_OUTPUT_LIMIT.value,
        "unsupported_format": FileTerminal.UNSUPPORTED_FORMAT.value,
    }.get(status)
    if (
        mapped is None
        or version != DEFUSEDXML_VERSION
        or header["detail"] not in FAILURE_DETAILS.get(status, set())
        or format_name not in ({"ooxml"} | SUPPORTED_FORMATS)
        or header["logical_unit_count"] != 0
        or header["primary_unit_count"] != 0
        or header["member_count"] != 0
        or header["expanded_bytes"] != 0
    ):
        return _invalid()
    return DecodedOoxml(
        mapped, format_name, detail=header["detail"],
        parser_version=DEFUSEDXML_VERSION,
    )


def _valid_header_scalars(header: dict[str, object], body: bytes) -> bool:
    return not (
        type(header["status"]) is not str
        or type(header["format"]) is not str
        or (header["detail"] is not None and type(header["detail"]) is not str)
        or (header["defusedxml_version"] is not None
            and type(header["defusedxml_version"]) is not str)
        or type(header["logical_unit_count"]) is not int
        or not 0 <= header["logical_unit_count"] <= MAX_LOGICAL_UNITS
        or type(header["member_count"]) is not int
        or not 0 <= header["member_count"] <= MAX_MEMBERS
        or type(header["primary_unit_count"]) is not int
        or not 0 <= header["primary_unit_count"] <= MAX_LOGICAL_UNITS
        or type(header["expanded_bytes"]) is not int
        or not 0 <= header["expanded_bytes"] <= MAX_EXPANDED_BYTES
        or type(header["text_bytes"]) is not int
        or type(header["text_chars"]) is not int
        or header["text_bytes"] < 0
        or header["text_chars"] < 0
        or type(header["units"]) is not list
        or len(body) != header["text_bytes"]
    )


def _decode_units(
    raw: object, format_name: object, primary_count: object,
) -> tuple[OoxmlUnit, ...] | None:
    if type(raw) is not list or len(raw) > MAX_LOGICAL_UNITS:
        return None
    units: list[OoxmlUnit] = []
    seen: set[tuple[str, str]] = set()
    for value in raw:
        if type(value) is not dict or set(value) != {"kind", "label", "text_chars"}:
            return None
        kind = value["kind"]
        label = value["label"]
        count = value["text_chars"]
        if (
            type(kind) is not str
            or kind not in FORMAT_UNIT_KINDS.get(format_name, set())
            or type(label) is not str
            or not 1 <= len(label) <= MAX_UNIT_LABEL_CHARS
            or not _canonical_label(format_name, kind, label, primary_count)
            or _unsafe_text(label, allow_separator=False)
            or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in label)
            or type(count) is not int
            or count < 0
        ):
            return None
        identity = (kind, label)
        if identity in seen:
            return None
        seen.add(identity)
        units.append(OoxmlUnit(kind, label, count))
    if format_name == "docx":
        parts = {unit.label.rpartition("#p")[0] for unit in units}
        if type(primary_count) is not int or len(parts) > primary_count:
            return None
    return tuple(units)


def _valid_primary_count(format_name: str, count: int) -> bool:
    limits = {
        "docx": MAX_MEMBERS, "xlsx": MAX_SHEETS, "pptx": MAX_SLIDES,
    }
    return (
        count <= limits[format_name]
        and (format_name != "docx" or count >= 1)
    )


def _canonical_label(
    format_name: object, kind: str, label: str, primary_count: object,
) -> bool:
    if format_name == "docx":
        prefix, marker, number = label.rpartition("#p")
        segments = prefix.split("/")
        return bool(
            marker
            and number.isascii()
            and number.isdigit()
            and number[0] != "0"
            and int(number) <= MAX_XML_ELEMENTS_PER_PART
            and (prefix == "main" or (
                prefix.startswith("word/")
                and prefix.endswith(".xml")
                and "\\" not in prefix
                and all(segment not in {"", ".", ".."} for segment in segments)
                and not re.search(r"%(?:2e|2f|5c)", prefix, re.IGNORECASE)
            ))
        )
    if format_name == "xlsx":
        match = re.fullmatch(
            r"sheet-([1-9][0-9]*)!([A-Z]{1,3})([1-9][0-9]{0,6})", label
        )
        if match is None:
            return False
        sheet, column, row = match.groups()
        column_number = 0
        for char in column:
            column_number = column_number * 26 + ord(char) - 64
        return (
            type(primary_count) is int
            and int(sheet) <= primary_count <= MAX_SHEETS
            and column_number <= 16_384
            and int(row) <= 1_048_576
        )
    if format_name == "pptx":
        suffix = {"slide": "", "notes": "-notes", "comments": "-comments"}[kind]
        match = re.fullmatch(r"slide-([1-9][0-9]*)" + suffix, label)
        return bool(
            match is not None
            and type(primary_count) is int
            and int(match.group(1)) <= primary_count <= MAX_SLIDES
        )
    return False


def _unsafe_text(text: str, *, allow_separator: bool) -> bool:
    allowed = "\t\n\r\f" if allow_separator else "\t\n\r"
    return any(
        char == "\x00"
        or ord(char) < 32 and char not in allowed
        or 127 <= ord(char) < 160
        for char in text
    )


def _invalid() -> DecodedOoxml:
    return DecodedOoxml(FileTerminal.PARSE_ERROR.value)


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
