"""C7B sandboxed legacy Excel extraction contracts."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from experimental.analyst import extract, xls_child
from experimental.analyst.extract import ExtractionResult, extract_document
from experimental.analyst.formats import DocumentFormat, sniff_document_format
from experimental.analyst.legacy_contract import (
    ANTIWORD_PACKAGE_REVISION,
    ANTIWORD_VERSION,
    FRAME_MAGIC as LEGACY_FRAME_MAGIC,
)
from experimental.analyst.models import FileTerminal
from experimental.analyst.sandbox import SandboxResult, _inventory_for_fd
from experimental.analyst.xls_contract import (
    CALAMINE_VERSION,
    FRAME_MAGIC,
    MAX_CALAMINE_INT,
    MAX_CELL_CHARS,
    MAX_CELLS,
    MAX_SHEETS,
    MIN_CALAMINE_INT,
    PYTHON_CALAMINE_VERSION,
    UNIT_SEPARATOR,
)
from experimental.analyst.xls_frame import decode_xls_frame


def _frame(header: dict, body: bytes = b"") -> bytes:
    return FRAME_MAGIC + json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii") + b"\n" + body


def _unit(label: str, scalar_type: str, text: str) -> dict:
    return {
        "kind": "cell",
        "label": label,
        "scalar_type": scalar_type,
        "text_chars": len(text),
    }


def _header(**changes) -> dict:
    values = ("Alpha", "42", "TRUE", "date:2026-08-16")
    text = UNIT_SEPARATOR.join(values)
    header = {
        "calamine_version": CALAMINE_VERSION,
        "dense_cell_count": 5,
        "detail": None,
        "format": "xls",
        "logical_unit_count": 4,
        "python_calamine_version": PYTHON_CALAMINE_VERSION,
        "sheet_count": 3,
        "skipped_sheet_count": 1,
        "status": "success",
        "text_bytes": len(text.encode("utf-8")),
        "text_chars": len(text),
        "units": [
            _unit("sheet-1!A1", "string", values[0]),
            _unit("sheet-1!B1", "int", values[1]),
            _unit("sheet-1!B2", "bool", values[2]),
            _unit("sheet-3!IV65536", "date", values[3]),
        ],
        "worksheet_count": 2,
    }
    header.update(changes)
    return header


def _valid_body() -> bytes:
    return "Alpha\f42\fTRUE\fdate:2026-08-16".encode()


def _extract_path(path: Path) -> ExtractionResult:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)


def _legacy_frame(status: str, detail: str | None, body: bytes = b"") -> bytes:
    text = body.decode("utf-8") if body else ""
    units = [] if not body else [{
        "kind": "output_line", "label": "output-line-1",
        "text_chars": len(text),
    }]
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
    return LEGACY_FRAME_MAGIC + json.dumps(
        header, sort_keys=True, separators=(",", ":"),
    ).encode("ascii") + b"\n" + body


def _failure_header(status: str, detail: str, **changes) -> dict:
    header = {
        "calamine_version": CALAMINE_VERSION,
        "dense_cell_count": 0,
        "detail": detail,
        "format": "xls",
        "logical_unit_count": 0,
        "python_calamine_version": PYTHON_CALAMINE_VERSION,
        "sheet_count": 0,
        "skipped_sheet_count": 0,
        "status": status,
        "text_bytes": 0,
        "text_chars": 0,
        "units": [],
        "worksheet_count": 0,
    }
    header.update(changes)
    return header


def test_cfb_magic_remains_only_a_legacy_office_candidate() -> None:
    signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    assert sniff_document_format(signature) is DocumentFormat.LEGACY_OFFICE
    assert sniff_document_format(signature + b"not-authenticated-yet") is \
        DocumentFormat.LEGACY_OFFICE


def test_strict_xls_frame_preserves_scalar_and_cell_provenance() -> None:
    decoded = decode_xls_frame(
        _frame(_header(), _valid_body()),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert decoded.reason == "success" and decoded.format_name == "xls"
    assert decoded.text == "Alpha\f42\fTRUE\fdate:2026-08-16"
    assert decoded.parser_version == PYTHON_CALAMINE_VERSION
    assert decoded.embedded_version == CALAMINE_VERSION
    assert decoded.logical_unit_count == 4
    assert (decoded.sheet_count, decoded.worksheet_count) == (3, 2)
    assert decoded.skipped_sheet_count == 1 and decoded.dense_cell_count == 5
    assert [
        (unit.kind, unit.label, unit.scalar_type, unit.char_count)
        for unit in decoded.units
    ] == [
        ("cell", "sheet-1!A1", "string", 5),
        ("cell", "sheet-1!B1", "int", 2),
        ("cell", "sheet-1!B2", "bool", 4),
        ("cell", "sheet-3!IV65536", "date", 15),
    ]


@pytest.mark.parametrize(
    ("scalar_type", "value"),
    [
        ("string", " text\tkept\nverbatim "),
        ("bool", "FALSE"),
        ("int", "-42"),
        ("int", str(MIN_CALAMINE_INT)),
        ("int", str(MAX_CALAMINE_INT)),
        ("float", repr(1.25)),
        ("date", "date:2026-08-16"),
        ("time", "time:01:02:03.000004"),
        ("datetime", "datetime:2026-08-16T01:02:03.000004"),
        ("duration", "duration_us:-90000000001"),
    ],
)
def test_strict_frame_accepts_only_canonical_scalar_text(
    scalar_type: str, value: str,
) -> None:
    header = _header(
        dense_cell_count=1,
        logical_unit_count=1,
        sheet_count=1,
        skipped_sheet_count=0,
        text_bytes=len(value.encode()),
        text_chars=len(value),
        units=[_unit("sheet-1!A1", scalar_type, value)],
        worksheet_count=1,
    )
    decoded = decode_xls_frame(
        _frame(header, value.encode()),
        max_text_bytes=1000,
        max_text_chars=1000,
    )
    assert decoded.reason == "success"
    assert decoded.units[0].scalar_type == scalar_type


@pytest.mark.parametrize(
    ("scalar_type", "value"),
    [
        ("bool", "true"),
        ("bool", "1"),
        ("int", "+1"),
        ("int", "01"),
        ("int", "-0"),
        ("int", str(MIN_CALAMINE_INT - 1)),
        ("int", str(MAX_CALAMINE_INT + 1)),
        ("float", "nan"),
        ("float", "inf"),
        ("float", "1.250"),
        ("date", "2026-08-16"),
        ("date", "date:2026-8-16"),
        ("time", "time:01:02:03"),
        ("time", "time:01:02:03.000004+00:00"),
        ("datetime", "datetime:2026-08-16T01:02:03"),
        ("duration", "duration_us:+1"),
        ("duration", "duration_us:01"),
        ("made_up", "x"),
    ],
)
def test_strict_frame_rejects_noncanonical_scalar_text(
    scalar_type: str, value: str,
) -> None:
    header = _header(
        dense_cell_count=1,
        logical_unit_count=1,
        sheet_count=1,
        skipped_sheet_count=0,
        text_bytes=len(value.encode()),
        text_chars=len(value),
        units=[_unit("sheet-1!A1", scalar_type, value)],
        worksheet_count=1,
    )
    assert decode_xls_frame(
        _frame(header, value.encode()),
        max_text_bytes=1000,
        max_text_chars=1000,
    ).reason == FileTerminal.PARSE_ERROR.value


@pytest.mark.parametrize(
    ("status", "detail", "reason"),
    [
        ("encrypted", "password_required", FileTerminal.ENCRYPTED.value),
        ("unsupported_format", "not_xls", FileTerminal.UNSUPPORTED_FORMAT.value),
        ("parse_error", "calamine_failed", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "control_character", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "input_alias", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "scalar_type", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "sheet_metadata", FileTerminal.PARSE_ERROR.value),
        ("parse_error", "sheet_shape", FileTerminal.PARSE_ERROR.value),
        ("parse_oom", "memory_limit", FileTerminal.PARSE_OOM.value),
        (
            "parser_output_limit", "cell_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "cell_text_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "dimension_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "semantic_unit_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "sheet_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
        (
            "parser_output_limit", "text_limit",
            FileTerminal.PARSER_OUTPUT_LIMIT.value,
        ),
    ],
)
def test_strict_xls_failure_vocabulary(
    status: str, detail: str, reason: str,
) -> None:
    decoded = decode_xls_frame(
        _frame(_failure_header(status, detail)),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert (decoded.reason, decoded.detail, decoded.text) == (reason, detail, None)
    assert decoded.parser_version == PYTHON_CALAMINE_VERSION
    assert decoded.embedded_version == CALAMINE_VERSION


@pytest.mark.parametrize("detail", ["dependency_missing", "dependency_version"])
def test_dependency_failure_omits_untrusted_versions(
    detail: str,
) -> None:
    decoded = decode_xls_frame(
        _frame(_failure_header(
            "dependency_unavailable", detail,
            python_calamine_version=None,
            calamine_version=None,
        )),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert (decoded.reason, decoded.detail) == (
        FileTerminal.SANDBOX_UNAVAILABLE.value, detail,
    )
    assert decoded.parser_version is None and decoded.embedded_version is None

    forged = decode_xls_frame(
        _frame(_failure_header(
            "dependency_unavailable", detail,
            python_calamine_version="0.8.1",
            calamine_version="0.35.0",
        )),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert forged.reason == FileTerminal.PARSE_ERROR.value
    assert forged.detail is None


def test_blank_authenticated_workbook_is_success_without_invented_content() -> None:
    decoded = decode_xls_frame(
        _frame(_header(
            dense_cell_count=0,
            logical_unit_count=0,
            sheet_count=2,
            skipped_sheet_count=1,
            text_bytes=0,
            text_chars=0,
            units=[],
            worksheet_count=1,
        )),
        max_text_bytes=100,
        max_text_chars=100,
    )
    assert decoded.reason == "success" and decoded.text == ""
    assert decoded.units == () and decoded.logical_unit_count == 0
    assert (decoded.sheet_count, decoded.worksheet_count) == (2, 1)


@pytest.mark.parametrize(
    ("header", "body"),
    [
        (_header(python_calamine_version="0.8.1"), _valid_body()),
        (_header(calamine_version="0.35.0"), _valid_body()),
        (_header(format="xlsx"), _valid_body()),
        (_header(logical_unit_count=True), _valid_body()),
        (_header(logical_unit_count=3), _valid_body()),
        (_header(sheet_count=MAX_SHEETS + 1), _valid_body()),
        (_header(worksheet_count=3, skipped_sheet_count=1), _valid_body()),
        (_header(dense_cell_count=3), _valid_body()),
        (_header(dense_cell_count=MAX_CELLS + 1), _valid_body()),
        (_header(text_bytes=True), _valid_body()),
        (_header(text_chars=28), _valid_body()),
        (_header(detail="calamine_failed"), _valid_body()),
        (_header(extra="nope"), _valid_body()),
        (
            _header(units=[
                _unit("sheet-1!B1", "string", "Alpha"),
                _unit("sheet-1!A1", "int", "42"),
                _unit("sheet-1!B2", "bool", "TRUE"),
                _unit("sheet-3!IV65536", "date", "date:2026-08-16"),
            ]),
            _valid_body(),
        ),
        (
            _header(units=[
                _unit("sheet-1!A1", "string", "Alpha"),
                _unit("sheet-1!B1", "int", "42"),
                _unit("sheet-1!B1", "bool", "TRUE"),
                _unit("sheet-3!IV65536", "date", "date:2026-08-16"),
            ]),
            _valid_body(),
        ),
        (
            _header(units=[
                _unit("sheet-1!A1", "string", "Alpha"),
                _unit("sheet-1!B1", "int", "42"),
                _unit("sheet-1!B2", "bool", "TRUE"),
                _unit("sheet-4!A1", "date", "date:2026-08-16"),
            ]),
            _valid_body(),
        ),
        (
            _header(units=[
                _unit("sheet-1!A1", "string", "Alpha"),
                _unit("sheet-1!B1", "int", "42"),
                _unit("sheet-1!B2", "bool", "TRUE"),
                _unit("sheet-3!IW1", "date", "date:2026-08-16"),
            ]),
            _valid_body(),
        ),
        (
            _header(units=[
                _unit("sheet-1!A1", "string", "Alpha"),
                _unit("sheet-1!B1", "int", "42"),
                _unit("sheet-1!B2", "bool", "TRUE"),
                _unit("sheet-3!A65537", "date", "date:2026-08-16"),
            ]),
            _valid_body(),
        ),
        (_header(), b"Alpha\n42\fTRUE\fdate:2026-08-16"),
        (_header(), b"Alpha\x0042\fTRUE\fdate:2026-08-16"),
        (_header(), b"Alpha\xff42\fTRUE\fdate:2026-08-16"),
    ],
)
def test_strict_success_frame_fails_closed(header: dict, body: bytes) -> None:
    decoded = decode_xls_frame(
        _frame(header, body), max_text_bytes=100, max_text_chars=100,
    )
    assert decoded.reason == FileTerminal.PARSE_ERROR.value
    assert decoded.text is None and decoded.units == ()


def test_per_cell_text_limit_is_revalidated_by_durable_decoder() -> None:
    value = "x" * (MAX_CELL_CHARS + 1)
    header = _header(
        dense_cell_count=1,
        logical_unit_count=1,
        sheet_count=1,
        skipped_sheet_count=0,
        text_bytes=len(value),
        text_chars=len(value),
        units=[_unit("sheet-1!A1", "string", value)],
        worksheet_count=1,
    )
    assert decode_xls_frame(
        _frame(header, value.encode()),
        max_text_bytes=len(value),
        max_text_chars=len(value),
    ).reason == FileTerminal.PARSE_ERROR.value


def test_failure_frame_cannot_smuggle_metadata_text_or_unknown_details() -> None:
    invalid = [
        (_failure_header("parse_error", "made_up"), b""),
        (_failure_header("success", "calamine_failed"), b""),
        (_failure_header("parse_error", "calamine_failed", text_bytes=1), b"x"),
        (_failure_header("parse_error", "calamine_failed", text_chars=1), b""),
        (_failure_header("parse_error", "calamine_failed", sheet_count=1), b""),
        (_failure_header("parse_error", "calamine_failed", dense_cell_count=1), b""),
        (_failure_header("parse_error", "calamine_failed", units=[
            _unit("sheet-1!A1", "string", "x"),
        ]), b""),
        (_failure_header("parse_error", "calamine_failed", calamine_version="x"), b""),
    ]
    for header, body in invalid:
        decoded = decode_xls_frame(
            _frame(header, body), max_text_bytes=100, max_text_chars=100,
        )
        assert decoded.reason == FileTerminal.PARSE_ERROR.value
        assert decoded.detail is None


def test_duplicate_keys_and_seeded_hostile_frames_never_escape_decoder() -> None:
    duplicate = FRAME_MAGIC + (
        b'{"python_calamine_version":"0.8.2",'
        b'"python_calamine_version":"0.8.2"}\n'
    )
    assert decode_xls_frame(
        duplicate, max_text_bytes=100, max_text_chars=100,
    ).reason == FileTerminal.PARSE_ERROR.value

    rng = random.Random(20260816)
    for _ in range(1000):
        payload = rng.randbytes(rng.randrange(0, 1025))
        assert decode_xls_frame(
            payload, max_text_bytes=100, max_text_chars=100,
        ).reason == FileTerminal.PARSE_ERROR.value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" exact text ", ("string", " exact text ")),
        (True, ("bool", "TRUE")),
        (False, ("bool", "FALSE")),
        (42, ("int", "42")),
        (-42, ("int", "-42")),
        (MIN_CALAMINE_INT, ("int", str(MIN_CALAMINE_INT))),
        (MAX_CALAMINE_INT, ("int", str(MAX_CALAMINE_INT))),
        (1.25, ("float", "1.25")),
        (dt.date(2026, 8, 16), ("date", "date:2026-08-16")),
        (dt.time(1, 2, 3, 4), ("time", "time:01:02:03.000004")),
        (
            dt.datetime(2026, 8, 16, 1, 2, 3, 4),
            ("datetime", "datetime:2026-08-16T01:02:03.000004"),
        ),
        (dt.timedelta(days=-2, microseconds=3), ("duration", "duration_us:-172799999997")),
        ("", None),
        (" \t\r\n", None),
    ],
)
def test_child_scalar_coercion_is_explicit_and_deterministic(
    value: object, expected: tuple[str, str] | None,
) -> None:
    assert xls_child._render_scalar(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        float("nan"), float("inf"), float("-inf"),
        dt.time(1, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc),
        MIN_CALAMINE_INT - 1, MAX_CALAMINE_INT + 1,
        b"bytes", None, complex(1, 2), object(),
    ],
)
def test_child_rejects_ambiguous_or_unsupported_scalars(value: object) -> None:
    with pytest.raises(xls_child.ChildFailure, match="scalar_type"):
        xls_child._render_scalar(value)


def test_child_rejects_scalar_subclasses_instead_of_coercing_them() -> None:
    class IntSubclass(int):
        pass

    class DateSubclass(dt.date):
        pass

    for value in (IntSubclass(1), DateSubclass(2026, 8, 16)):
        with pytest.raises(xls_child.ChildFailure, match="scalar_type"):
            xls_child._render_scalar(value)


@pytest.mark.parametrize("value", ["bad\x00", "bad\x01", "bad\x7f", "bad\x85"])
def test_child_rejects_unsafe_cell_controls(value: str) -> None:
    with pytest.raises(xls_child.ChildFailure, match="control_character"):
        xls_child._render_scalar(value)


def test_child_column_labels_cover_the_exact_biff8_boundary() -> None:
    assert [(number, xls_child._column_label(number)) for number in (
        1, 26, 27, 52, 53, 255, 256,
    )] == [
        (1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"),
        (53, "BA"), (255, "IU"), (256, "IV"),
    ]
    for number in (0, -1, 257):
        with pytest.raises(xls_child.ChildFailure, match="sheet_shape"):
            xls_child._column_label(number)


def test_child_output_enforces_cell_semantic_and_text_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact = xls_child.Output(5, 3)
    exact.add("sheet-1!A1", "string", "α")
    exact.add("sheet-1!B1", "string", "b")
    assert exact.finish() == "α\fb"
    assert (exact.byte_count, exact.char_count) == (4, 3)
    with pytest.raises(xls_child.ChildFailure, match="text_limit"):
        exact.add("sheet-1!C1", "string", "c")

    monkeypatch.setattr(xls_child, "MAX_CELL_CHARS", 2)
    with pytest.raises(xls_child.ChildFailure, match="cell_text_limit"):
        xls_child.Output(100, 100).add("sheet-1!A1", "string", "abc")

    monkeypatch.setattr(xls_child, "MAX_LOGICAL_UNITS", 1)
    capped = xls_child.Output(100, 100)
    capped.add("sheet-1!A1", "string", "a")
    with pytest.raises(xls_child.ChildFailure, match="semantic_unit_limit"):
        capped.add("sheet-1!B1", "string", "b")


def test_child_extracts_all_worksheet_visibility_in_original_order() -> None:
    calamine = _fake_calamine()
    visible = _FakeSheet((1, 2), (2, 3), [
        ["cached", 42], ["", True],
    ])
    macro = _FakeSheet((0, 0), (0, 0), [["must not be read"]])
    hidden = _FakeSheet((0, 0), (0, 0), [[dt.date(2026, 8, 16)]])
    empty = _EmptySheet()
    workbook = _FakeWorkbook(
        calamine,
        [
            ("Visible", calamine.SheetTypeEnum.WorkSheet,
             calamine.SheetVisibleEnum.Visible, visible),
            ("Macro", calamine.SheetTypeEnum.MacroSheet,
             calamine.SheetVisibleEnum.Visible, macro),
            ("Hidden", calamine.SheetTypeEnum.WorkSheet,
             calamine.SheetVisibleEnum.Hidden, hidden),
            ("VeryHiddenEmpty", calamine.SheetTypeEnum.WorkSheet,
             calamine.SheetVisibleEnum.VeryHidden, empty),
        ],
    )
    output, sheets, worksheets, skipped, dense = xls_child._extract_workbook(
        calamine, workbook, 1000, 1000,
    )
    assert (sheets, worksheets, skipped, dense) == (4, 3, 1, 5)
    assert output.finish() == "cached\f42\fTRUE\fdate:2026-08-16"
    assert [(unit.label, unit.scalar_type) for unit in output.units] == [
        ("sheet-1!C2", "string"),
        ("sheet-1!D2", "int"),
        ("sheet-1!D3", "bool"),
        ("sheet-3!A1", "date"),
    ]
    assert visible.calls == [(True, 2)] and hidden.calls == [(True, 1)]
    assert empty.calls == []
    assert workbook.requested == [0, 2, 3]


def test_formula_cells_are_only_the_cached_typed_scalar() -> None:
    calamine = _fake_calamine()
    # python-calamine exposes only this stored result; no formula API is called.
    sheet = _FakeSheet((0, 0), (0, 0), [[65.0]])
    workbook = _FakeWorkbook(calamine, [(
        "Formula", calamine.SheetTypeEnum.WorkSheet,
        calamine.SheetVisibleEnum.Visible, sheet,
    )])
    output, *_counts = xls_child._extract_workbook(calamine, workbook, 100, 100)
    assert output.finish() == "65.0"
    assert output.units[0].scalar_type == "float"
    assert not hasattr(sheet, "formulas")


def test_blank_error_and_literal_empty_collapse_is_explicit() -> None:
    calamine = _fake_calamine()
    # The wrapper represents blank cells, error cells and literal empty strings
    # identically as ''. The extractor must not invent distinctions or findings.
    sheet = _FakeSheet((0, 0), (0, 2), [["", "", ""]])
    workbook = _FakeWorkbook(calamine, [(
        "Collapsed", calamine.SheetTypeEnum.WorkSheet,
        calamine.SheetVisibleEnum.Visible, sheet,
    )])
    output, sheets, worksheets, skipped, dense = xls_child._extract_workbook(
        calamine, workbook, 100, 100,
    )
    assert output.finish() == "" and output.units == []
    assert (sheets, worksheets, skipped, dense) == (1, 1, 0, 3)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda wb: setattr(wb, "sheet_names", "not-a-list"),
        lambda wb: wb.sheet_names.append("extra"),
        lambda wb: setattr(wb.sheets_metadata[0], "name", "mismatch"),
        lambda wb: setattr(wb.sheets_metadata[0], "typ", object()),
        lambda wb: setattr(wb.sheets_metadata[0], "visible", object()),
    ],
)
def test_child_rejects_untrusted_sheet_metadata(mutate) -> None:
    calamine = _fake_calamine()
    workbook = _one_sheet_workbook(calamine, _FakeSheet((0, 0), (0, 0), [["x"]]))
    mutate(workbook)
    with pytest.raises(xls_child.ChildFailure, match="sheet_metadata"):
        xls_child._extract_workbook(calamine, workbook, 100, 100)


@pytest.mark.parametrize(
    "case",
    [
        ((0, 0), (0, 0), [["x"]], {"height": True}),
        ((0, 0), (0, 0), [["x"]], {"width": True}),
        ((0, 0), (1, 0), [["x"]], {"height": 1}),
        ((0, 0), (0, 1), [["x"]], {"width": 1}),
        ((0, 0), (65536, 0), [["x"]], {"height": 65537}),
        ((0, 0), (0, 256), [["x"]], {"width": 257}),
        (None, (0, 0), [["x"]], {}),
        ((0, 0), None, [["x"]], {}),
    ],
)
def test_child_rejects_invalid_or_oversized_sheet_shapes(case: tuple) -> None:
    calamine = _fake_calamine()
    start, end, rows, options = case
    sheet = _FakeSheet(start, end, rows, **options)
    workbook = _one_sheet_workbook(calamine, sheet)
    with pytest.raises(xls_child.ChildFailure) as caught:
        xls_child._extract_workbook(calamine, workbook, 100, 100)
    assert caught.value.detail in {"dimension_limit", "sheet_shape"}


def test_child_enforces_exact_sheet_and_dense_cell_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calamine = _fake_calamine()
    sheet = _FakeSheet((0, 0), (1, 1), [["", ""], ["", ""]])
    workbook = _one_sheet_workbook(calamine, sheet)
    monkeypatch.setattr(xls_child, "MAX_CELLS", 4)
    assert xls_child._extract_workbook(calamine, workbook, 100, 100)[4] == 4
    monkeypatch.setattr(xls_child, "MAX_CELLS", 3)
    with pytest.raises(xls_child.ChildFailure, match="cell_limit"):
        xls_child._extract_workbook(calamine, workbook, 100, 100)

    monkeypatch.setattr(xls_child, "MAX_SHEETS", 0)
    with pytest.raises(xls_child.ChildFailure, match="sheet_limit"):
        xls_child._extract_workbook(calamine, workbook, 100, 100)


def test_child_rejects_wrong_row_container_and_width() -> None:
    calamine = _fake_calamine()
    for rows in ((["x"],), [["x", "extra"]], []):
        sheet = _FakeSheet((0, 0), (0, 0), rows)
        workbook = _one_sheet_workbook(calamine, sheet)
        with pytest.raises(xls_child.ChildFailure, match="sheet_shape"):
            xls_child._extract_workbook(calamine, workbook, 100, 100)


def test_child_uses_fixed_private_xls_alias_and_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "extensionless-input"
    alias = tmp_path / "private" / "document.xls"
    alias.parent.mkdir()
    source.write_bytes(b"public")
    calamine = _fake_calamine()
    workbook = SimpleNamespace(close=lambda: None)
    observed: dict[str, object] = {}

    class Loader:
        @classmethod
        def from_path(cls, path, *, load_tables):
            observed.update(path=path, load_tables=load_tables)
            assert Path(path).is_symlink()
            assert Path(path).resolve() == source
            return workbook

    calamine.CalamineWorkbook = Loader
    monkeypatch.setattr(xls_child, "INPUT_PATH", source)
    monkeypatch.setattr(xls_child, "XLS_INPUT_PATH", alias)
    sentinel = (xls_child.Output(10, 10), 0, 0, 0, 0)
    monkeypatch.setattr(xls_child, "_extract_workbook", lambda *_a: sentinel)

    assert xls_child._extract(calamine, 10, 10) is sentinel
    assert observed == {"path": alias, "load_tables": False}
    assert not alias.exists() and not alias.is_symlink()


@pytest.mark.parametrize(
    ("exception_name", "message", "status", "detail"),
    [
        ("PasswordError", "Workbook is password protected", "encrypted", "password_required"),
        ("CalamineError", "Cfb error: Cannot find Workbook stream", "unsupported_format", "not_xls"),
        ("CalamineError", "Cannot detect file format", "unsupported_format", "not_xls"),
        ("CalamineError", "other parser failure", "parse_error", "calamine_failed"),
    ],
)
def test_child_maps_only_closed_calamine_open_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_name: str,
    message: str,
    status: str,
    detail: str,
) -> None:
    source = tmp_path / "input"
    alias = tmp_path / "document.xls"
    source.write_bytes(b"public")
    calamine = _fake_calamine()
    error = getattr(calamine, exception_name)

    class Loader:
        @classmethod
        def from_path(cls, _path, *, load_tables):
            assert load_tables is False
            raise error(message)

    calamine.CalamineWorkbook = Loader
    monkeypatch.setattr(xls_child, "INPUT_PATH", source)
    monkeypatch.setattr(xls_child, "XLS_INPUT_PATH", alias)
    with pytest.raises(xls_child.ChildFailure) as caught:
        xls_child._extract(calamine, 100, 100)
    assert (caught.value.status, caught.value.detail) == (status, detail)
    assert not alias.exists() and not alias.is_symlink()


def test_child_refuses_preexisting_alias_without_overwriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input"
    alias = tmp_path / "document.xls"
    source.write_bytes(b"public")
    alias.write_text("guard", encoding="ascii")
    monkeypatch.setattr(xls_child, "INPUT_PATH", source)
    monkeypatch.setattr(xls_child, "XLS_INPUT_PATH", alias)
    with pytest.raises(xls_child.ChildFailure, match="input_alias"):
        xls_child._extract(_fake_calamine(), 100, 100)
    assert alias.read_text(encoding="ascii") == "guard"


def test_legacy_dispatch_falls_through_only_from_exact_not_word_to_xls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "renamed.bin"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1candidate")
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ("doc-bind",))
    monkeypatch.setattr(extract, "xls_runtime_binds", lambda: ("xls-bind",))

    def sandbox(**kwargs):
        observed.append(kwargs)
        if len(observed) == 1:
            payload = _legacy_frame(
                "unsupported_format", "not_word_binary",
            )
        else:
            payload = _frame(_header(), _valid_body())
        return SandboxResult("success", 0, payload, b"", "test.scope")

    monkeypatch.setattr(extract, "run_sandboxed", sandbox)
    result = _extract_path(path)
    assert result.ok and result.format_name == "xls"
    assert result.text == "Alpha\f42\fTRUE\fdate:2026-08-16"
    assert result.parser_version == PYTHON_CALAMINE_VERSION
    assert result.embedded_version == CALAMINE_VERSION
    assert result.primary_unit_count == 3
    assert result.worksheet_count == 2 and result.skipped_sheet_count == 1
    assert result.dense_cell_count == 5 and len(result.xls_units) == 4
    assert len(observed) == 2
    assert observed[0]["runtime_binds"] == ("doc-bind",)
    assert observed[1]["runtime_binds"] == ("xls-bind",)
    assert observed[0]["command"] == (
        str(Path(sys.executable).resolve()), "-I", "-B",
        str(extract.LEGACY_CHILD_DESTINATION), str(extract.MAX_TEXT_BYTES),
        str(extract.MAX_TEXT_CHARS),
    )
    assert observed[1]["command"] == (
        str(Path(sys.executable).resolve()), "-I", "-B",
        str(extract.XLS_CHILD_DESTINATION), str(extract.MAX_TEXT_BYTES),
        str(extract.MAX_TEXT_CHARS),
    )


@pytest.mark.parametrize(
    ("status", "detail", "expected"),
    [
        ("success", None, "success"),
        ("encrypted", "password_required", FileTerminal.ENCRYPTED.value),
        ("parse_error", "antiword_failed", FileTerminal.PARSE_ERROR.value),
        (
            "unsupported_format", "unsupported_word_variant",
            FileTerminal.UNSUPPORTED_FORMAT.value,
        ),
    ],
)
def test_non_not_word_doc_outcomes_never_reach_xls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    detail: str | None,
    expected: str,
) -> None:
    path = tmp_path / "candidate"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1candidate")
    body = b"Word text" if status == "success" else b""
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())
    monkeypatch.setattr(
        extract, "xls_runtime_binds",
        lambda: pytest.fail("non-fallthrough DOC outcome reached XLS"),
    )
    monkeypatch.setattr(
        extract, "run_sandboxed",
        lambda **_kwargs: SandboxResult(
            "success", 0, _legacy_frame(status, detail, body), b"", "test.scope",
        ),
    )
    result = _extract_path(path)
    assert result.reason == expected
    assert result.format_name == "doc"


def test_generic_cfb_rejected_by_both_parsers_is_explicitly_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "generic.ole"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1generic")
    calls = 0
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())
    monkeypatch.setattr(extract, "xls_runtime_binds", lambda: ())

    def sandbox(**_kwargs):
        nonlocal calls
        calls += 1
        payload = (
            _legacy_frame("unsupported_format", "not_word_binary")
            if calls == 1
            else _frame(_failure_header("unsupported_format", "not_xls"))
        )
        return SandboxResult("success", 0, payload, b"", "test.scope")

    monkeypatch.setattr(extract, "run_sandboxed", sandbox)
    result = _extract_path(path)
    assert calls == 2
    assert (result.reason, result.format_name, result.detail) == (
        FileTerminal.UNSUPPORTED_FORMAT.value,
        DocumentFormat.LEGACY_OFFICE.value,
        None,
    )


@pytest.mark.parametrize("detail", ["dependency_missing", "dependency_version"])
def test_xls_dependency_failure_stops_before_second_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str,
) -> None:
    path = tmp_path / "candidate"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1candidate")
    calls = 0
    monkeypatch.setattr(extract, "antiword_runtime_binds", lambda: ())

    def unavailable():
        raise extract.OptionalDependencyUnavailable(detail)

    monkeypatch.setattr(extract, "xls_runtime_binds", unavailable)

    def sandbox(**_kwargs):
        nonlocal calls
        calls += 1
        return SandboxResult(
            "success", 0,
            _legacy_frame("unsupported_format", "not_word_binary"),
            b"", "test.scope",
        )

    monkeypatch.setattr(extract, "run_sandboxed", sandbox)
    result = _extract_path(path)
    assert calls == 1
    assert (result.reason, result.format_name, result.detail) == (
        FileTerminal.SANDBOX_UNAVAILABLE.value, "xls", detail,
    )


def test_xls_runtime_bind_is_narrow_native_and_exact() -> None:
    package, init_path, extension = extract._python_calamine_runtime_files()
    bindings = extract.xls_runtime_binds()
    sources = {binding.source for binding in bindings}
    destinations = {binding.destination for binding in bindings}
    assert init_path in sources and extension in sources
    assert extract.XLS_CHILD_PATH in sources
    assert extract.XLS_CONTRACT_PATH in sources
    assert extract.XLS_CHILD_DESTINATION in destinations
    assert extract.XLS_CONTRACT_DESTINATION in destinations
    assert extract.XLS_SITE_DESTINATION / "__init__.py" in destinations
    assert (
        extract.XLS_SITE_DESTINATION / extract.PYTHON_CALAMINE_EXTENSION
    ) in destinations
    assert package not in sources
    assert Path(sys.prefix).resolve() not in sources
    assert Path("/usr") not in sources
    assert Path.home() not in sources
    assert extract.CHILD_PATH not in sources
    assert extract.PDF_CHILD_PATH not in sources
    assert extract.OOXML_CHILD_PATH not in sources
    assert extract.LEGACY_CHILD_PATH not in sources
    assert all(binding.source.exists() for binding in bindings)


def test_xls_runtime_rejects_digest_drift_before_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract, "_runtime_sha256", lambda *_args: "0" * 64)
    monkeypatch.setattr(
        extract, "_discover_xls_runtime_binds",
        lambda *_args: pytest.fail("digest mismatch reached runtime discovery"),
    )
    with pytest.raises(extract.OptionalDependencyUnavailable) as caught:
        extract.xls_runtime_binds()
    assert caught.value.detail == "dependency_version"


def test_xls_runtime_detects_identity_change_after_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _package, _init_path, extension = extract._python_calamine_runtime_files()
    original = extract._runtime_identity
    observations = 0

    def changing(path: Path):
        nonlocal observations
        identity = original(path)
        if path == extension:
            observations += 1
            if observations > 1:
                return (*identity[:3], identity[3] + 1)
        return identity

    monkeypatch.setattr(extract, "_runtime_identity", changing)
    with pytest.raises(RuntimeError, match="changed during discovery"):
        extract.xls_runtime_binds()


def test_python_calamine_import_exists_only_in_xls_child() -> None:
    package_dir = Path(extract.__file__).parent
    offenders: list[str] = []
    import ast

    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] == "python_calamine" for name in names):
                if path.name != "xls_child.py":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders

    source = (
        "import sys; import experimental.analyst; "
        "import experimental.analyst.extract; "
        "assert 'python_calamine' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", source],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
        shell=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")


def test_live_sandbox_reports_public_hash_pinned_encrypted_xls(
    tmp_path: Path,
) -> None:
    """Preserve the extension-sensitive PasswordError path through C3.

    The fixture is python-calamine v0.8.2's public
    ``tests/data/password.xls``, stored as reviewable base64 for offline tests.
    """
    try:
        extract._verify_antiword_package()
        extract.xls_runtime_binds()
    except extract.OptionalDependencyUnavailable as exc:
        pytest.skip(f"exact legacy dependency unavailable: {exc.detail}")

    fixture = (
        Path(__file__).parent / "fixtures" / "analyst_legacy"
        / "password_xls_v0_8_2.b64"
    )
    encoded = b"".join(fixture.read_bytes().splitlines())
    binary = base64.b64decode(encoded, validate=True)
    assert len(binary) == 6656
    assert hashlib.sha256(binary).hexdigest() == (
        "5aa155e8c345f500284a7cda284b463492c9dd5f23405a286c132e7babfb0e0d"
    )
    document = tmp_path / "encrypted-extensionless-candidate"
    document.write_bytes(binary)

    result = _extract_path(document)
    assert (result.reason, result.format_name, result.detail) == (
        FileTerminal.ENCRYPTED.value, "xls", "password_required",
    )
    assert result.text is None and result.xls_units == ()
    assert result.parser_version == PYTHON_CALAMINE_VERSION
    assert result.embedded_version == CALAMINE_VERSION


def test_live_sandbox_extracts_public_generated_xls(tmp_path: Path) -> None:
    soffice = shutil.which("soffice")
    if soffice is None:
        pytest.skip("LibreOffice is unavailable for public XLS fixture generation")
    try:
        extract._verify_antiword_package()
        extract.xls_runtime_binds()
    except extract.OptionalDependencyUnavailable as exc:
        pytest.skip(f"exact legacy dependency unavailable: {exc.detail}")

    source = tmp_path / "public.csv"
    output = tmp_path / "out"
    profile = tmp_path / "lo-profile"
    output.mkdir()
    profile.mkdir()
    source.write_text(
        "Name,Amount,Active\n"
        "Analyst Public XLS,42,TRUE\n"
        "Reserved example 192.0.2.10,1.25,FALSE\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            soffice, "--headless", "--nologo", "--nodefault", "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to", "xls:MS Excel 97", "--outdir", str(output),
            str(source),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
        shell=False,
        env={
            "HOME": str(tmp_path), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
    )
    document = output / "public.xls"
    if completed.returncode != 0 or not document.is_file():
        pytest.skip(
            "LibreOffice could not generate the isolated public XLS fixture: "
            + completed.stderr.decode("utf-8", "replace")[:200]
        )
    renamed = tmp_path / "renamed-without-extension"
    document.rename(renamed)

    result = _extract_path(renamed)
    assert result.ok, (result.reason, result.detail)
    assert result.format_name == "xls" and result.encoding == "utf-8"
    assert result.parser_version == PYTHON_CALAMINE_VERSION
    assert result.embedded_version == CALAMINE_VERSION
    assert result.text is not None
    assert "Analyst Public XLS" in result.text
    assert "192.0.2.10" in result.text and "42" in result.text
    assert result.logical_unit_count == len(result.xls_units) > 0
    assert result.primary_unit_count == result.worksheet_count == 1
    assert all(unit.label.startswith("sheet-1!") for unit in result.xls_units)


class _FakeError(Exception):
    pass


def _fake_calamine() -> SimpleNamespace:
    type_tokens = SimpleNamespace(
        ChartSheet=object(), DialogSheet=object(), MacroSheet=object(),
        Vba=object(), WorkSheet=object(),
    )
    visibility_tokens = SimpleNamespace(
        Hidden=object(), VeryHidden=object(), Visible=object(),
    )
    return SimpleNamespace(
        CalamineError=_FakeError,
        PasswordError=type("PasswordError", (_FakeError,), {}),
        SheetTypeEnum=type_tokens,
        SheetVisibleEnum=visibility_tokens,
    )


class _FakeSheet:
    def __init__(
        self,
        start: tuple[int, int] | None,
        end: tuple[int, int] | None,
        rows: object,
        *,
        height: object | None = None,
        width: object | None = None,
    ) -> None:
        self.start = start
        self.end = end
        self.rows = rows
        self.height = (
            height if height is not None
            else 0 if start is None or end is None else end[0] - start[0] + 1
        )
        self.width = (
            width if width is not None
            else 0 if start is None or end is None else end[1] - start[1] + 1
        )
        self.calls: list[tuple[object, object]] = []

    def to_python(self, *, skip_empty_area, nrows):
        self.calls.append((skip_empty_area, nrows))
        return self.rows


class _EmptySheet:
    start = None
    end = None
    height = 0
    width = 0

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def to_python(self, **kwargs):
        self.calls.append(tuple(kwargs.values()))
        raise AssertionError("empty sheet reached panic-prone parser iterator")


class _FakeWorkbook:
    def __init__(self, calamine: SimpleNamespace, rows: list[tuple]) -> None:
        self.sheet_names = [row[0] for row in rows]
        self.sheets_metadata = [
            SimpleNamespace(name=name, typ=kind, visible=visibility)
            for name, kind, visibility, _sheet in rows
        ]
        self.sheets = [row[3] for row in rows]
        self.requested: list[int] = []

    def get_sheet_by_index(self, index: int):
        self.requested.append(index)
        return self.sheets[index]


def _one_sheet_workbook(calamine: SimpleNamespace, sheet: object) -> _FakeWorkbook:
    return _FakeWorkbook(calamine, [(
        "Sheet", calamine.SheetTypeEnum.WorkSheet,
        calamine.SheetVisibleEnum.Visible, sheet,
    )])
