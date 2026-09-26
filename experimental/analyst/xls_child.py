"""Sandbox-only python-calamine adapter for bounded legacy XLS extraction."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

RUNTIME_PATH = "/runtime"
SITE_PATH = "/runtime/site-packages"
if __package__:
    from . import xls_contract as _contract
else:
    if RUNTIME_PATH not in sys.path:
        sys.path.insert(0, RUNTIME_PATH)
    import xls_contract as _contract  # type: ignore[import-not-found]

CALAMINE_VERSION = _contract.CALAMINE_VERSION
FRAME_MAGIC = _contract.FRAME_MAGIC
INPUT_PATH = _contract.INPUT_PATH
MAX_CELL_CHARS = _contract.MAX_CELL_CHARS
MAX_CELLS = _contract.MAX_CELLS
MAX_HEADER_BYTES = _contract.MAX_HEADER_BYTES
MAX_CALAMINE_INT = _contract.MAX_CALAMINE_INT
MAX_LOGICAL_UNITS = _contract.MAX_LOGICAL_UNITS
MAX_SHEETS = _contract.MAX_SHEETS
MAX_XLS_COLUMNS = _contract.MAX_XLS_COLUMNS
MAX_XLS_ROWS = _contract.MAX_XLS_ROWS
MIN_CALAMINE_INT = _contract.MIN_CALAMINE_INT
PYTHON_CALAMINE_EXTENSION = _contract.PYTHON_CALAMINE_EXTENSION
PYTHON_CALAMINE_EXTENSION_SHA256 = _contract.PYTHON_CALAMINE_EXTENSION_SHA256
PYTHON_CALAMINE_INIT_SHA256 = _contract.PYTHON_CALAMINE_INIT_SHA256
PYTHON_CALAMINE_VERSION = _contract.PYTHON_CALAMINE_VERSION
UNIT_SEPARATOR = _contract.UNIT_SEPARATOR
XLS_INPUT_PATH = _contract.XLS_INPUT_PATH

PACKAGE_PATH = Path(SITE_PATH) / "python_calamine"
NOT_XLS_ERRORS = {
    "Cfb error: Cannot find Workbook stream",
    "Cannot detect file format",
}


class ChildFailure(Exception):
    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Unit:
    kind: str
    label: str
    scalar_type: str
    text: str


class Output:
    def __init__(self, max_bytes: int, max_chars: int) -> None:
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.units: list[Unit] = []
        self.byte_count = 0
        self.char_count = 0

    def add(self, label: str, scalar_type: str, text: str) -> None:
        _safe_text(text)
        if not text.strip():
            return
        if len(text) > MAX_CELL_CHARS:
            raise ChildFailure("parser_output_limit", "cell_text_limit")
        if len(self.units) >= MAX_LOGICAL_UNITS:
            raise ChildFailure("parser_output_limit", "semantic_unit_limit")
        separator = 1 if self.units else 0
        encoded = text.encode("utf-8", errors="strict")
        if (
            self.byte_count + separator + len(encoded) > self.max_bytes
            or self.char_count + separator + len(text) > self.max_chars
        ):
            raise ChildFailure("parser_output_limit", "text_limit")
        self.units.append(Unit("cell", label, scalar_type, text))
        self.byte_count += separator + len(encoded)
        self.char_count += separator + len(text)

    def finish(self) -> str:
        return UNIT_SEPARATOR.join(unit.text for unit in self.units)


def _write_frame(
    status: str,
    *,
    detail: str | None = None,
    output: Output | None = None,
    sheet_count: int = 0,
    worksheet_count: int = 0,
    skipped_sheet_count: int = 0,
    dense_cell_count: int = 0,
    versions: bool = True,
) -> None:
    text = output.finish() if output is not None else ""
    body = text.encode("utf-8", errors="strict")
    units = [
        {
            "kind": unit.kind,
            "label": unit.label,
            "scalar_type": unit.scalar_type,
            "text_chars": len(unit.text),
        }
        for unit in (output.units if output is not None else ())
    ]
    header = {
        "calamine_version": CALAMINE_VERSION if versions else None,
        "dense_cell_count": dense_cell_count,
        "detail": detail,
        "format": "xls",
        "logical_unit_count": len(units),
        "python_calamine_version": PYTHON_CALAMINE_VERSION if versions else None,
        "sheet_count": sheet_count,
        "skipped_sheet_count": skipped_sheet_count,
        "status": status,
        "text_bytes": len(body),
        "text_chars": len(text),
        "units": units,
        "worksheet_count": worksheet_count,
    }
    encoded = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(encoded) > MAX_HEADER_BYTES:
        if status != "success":
            raise RuntimeError("failure frame exceeded fixed header limit")
        _write_frame(
            "parser_output_limit", detail="semantic_unit_limit", versions=versions
        )
        return
    sys.stdout.buffer.write(FRAME_MAGIC + encoded + b"\n" + body)
    sys.stdout.buffer.flush()


def _load_calamine():
    init_path = PACKAGE_PATH / "__init__.py"
    extension_path = PACKAGE_PATH / PYTHON_CALAMINE_EXTENSION
    try:
        init_digest = _file_sha256(init_path, 64 * 1024)
        extension_digest = _file_sha256(extension_path, 8 * 1024 * 1024)
    except OSError:
        _write_frame(
            "dependency_unavailable", detail="dependency_missing", versions=False
        )
        return None
    if (
        init_digest != PYTHON_CALAMINE_INIT_SHA256
        or extension_digest != PYTHON_CALAMINE_EXTENSION_SHA256
    ):
        _write_frame(
            "dependency_unavailable", detail="dependency_version", versions=False
        )
        return None
    if SITE_PATH not in sys.path:
        sys.path.insert(0, SITE_PATH)
    try:
        import python_calamine  # type: ignore[import-not-found]
    except Exception:
        _write_frame(
            "dependency_unavailable", detail="dependency_missing", versions=False
        )
        return None
    required = {
        "CalamineError", "CalamineWorkbook", "PasswordError",
        "SheetTypeEnum", "SheetVisibleEnum",
    }
    if any(not hasattr(python_calamine, name) for name in required):
        _write_frame(
            "dependency_unavailable", detail="dependency_version", versions=False
        )
        return None
    return python_calamine


def _file_sha256(path: Path, maximum: int) -> str:
    observed = path.stat()
    if not path.is_file() or observed.st_size <= 0 or observed.st_size > maximum:
        raise OSError("invalid runtime file")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OSError("runtime file exceeds limit")
            digest.update(chunk)
    if total != observed.st_size:
        raise OSError("runtime file changed")
    return digest.hexdigest()


def _extract(calamine, max_bytes: int, max_chars: int):
    try:
        os.symlink(INPUT_PATH, XLS_INPUT_PATH)
    except OSError as exc:
        raise ChildFailure("parse_error", "input_alias") from exc
    try:
        try:
            workbook = calamine.CalamineWorkbook.from_path(
                XLS_INPUT_PATH, load_tables=False
            )
        except calamine.PasswordError as exc:
            raise ChildFailure("encrypted", "password_required") from exc
        except calamine.CalamineError as exc:
            if str(exc) in NOT_XLS_ERRORS:
                raise ChildFailure("unsupported_format", "not_xls") from exc
            raise ChildFailure("parse_error", "calamine_failed") from exc
        try:
            result = _extract_workbook(calamine, workbook, max_bytes, max_chars)
        except calamine.CalamineError as exc:
            _close_quietly(workbook)
            raise ChildFailure("parse_error", "calamine_failed") from exc
        except BaseException:
            _close_quietly(workbook)
            raise
        try:
            workbook.close()
        except calamine.CalamineError as exc:
            raise ChildFailure("parse_error", "calamine_failed") from exc
        return result
    finally:
        try:
            XLS_INPUT_PATH.unlink()
        except OSError:
            pass


def _extract_workbook(calamine, workbook, max_bytes: int, max_chars: int):
    metadata = workbook.sheets_metadata
    names = workbook.sheet_names
    if type(metadata) is not list or type(names) is not list or len(metadata) != len(names):
        raise ChildFailure("parse_error", "sheet_metadata")
    sheet_count = len(metadata)
    if sheet_count > MAX_SHEETS:
        raise ChildFailure("parser_output_limit", "sheet_limit")
    known_types = (
        calamine.SheetTypeEnum.ChartSheet,
        calamine.SheetTypeEnum.DialogSheet,
        calamine.SheetTypeEnum.MacroSheet,
        calamine.SheetTypeEnum.Vba,
        calamine.SheetTypeEnum.WorkSheet,
    )
    known_visibility = (
        calamine.SheetVisibleEnum.Hidden,
        calamine.SheetVisibleEnum.VeryHidden,
        calamine.SheetVisibleEnum.Visible,
    )
    output = Output(max_bytes, max_chars)
    worksheet_count = 0
    skipped_sheet_count = 0
    dense_cell_count = 0
    for sheet_index, (item, name) in enumerate(zip(metadata, names), start=1):
        if (
            type(name) is not str
            or getattr(item, "name", None) != name
            or getattr(item, "typ", None) not in known_types
            or getattr(item, "visible", None) not in known_visibility
        ):
            raise ChildFailure("parse_error", "sheet_metadata")
        if item.typ != calamine.SheetTypeEnum.WorkSheet:
            skipped_sheet_count += 1
            continue
        worksheet_count += 1
        sheet = workbook.get_sheet_by_index(sheet_index - 1)
        start = sheet.start
        end = sheet.end
        height = sheet.height
        width = sheet.width
        if start is None or end is None:
            if not (start is None and end is None and height == 0 and width == 0):
                raise ChildFailure("parse_error", "sheet_shape")
            continue
        start_row, start_column = _coordinate(start)
        end_row, end_column = _coordinate(end)
        if (
            end_row < start_row
            or end_column < start_column
            or end_row >= MAX_XLS_ROWS
            or end_column >= MAX_XLS_COLUMNS
            or type(height) is not int
            or type(width) is not int
            or height != end_row - start_row + 1
            or width != end_column - start_column + 1
        ):
            raise ChildFailure("parser_output_limit", "dimension_limit")
        dense = height * width
        if dense_cell_count + dense > MAX_CELLS:
            raise ChildFailure("parser_output_limit", "cell_limit")
        dense_cell_count += dense
        try:
            rows = sheet.to_python(skip_empty_area=True, nrows=height)
        except calamine.CalamineError as exc:
            raise ChildFailure("parse_error", "calamine_failed") from exc
        if type(rows) is not list or len(rows) != height:
            raise ChildFailure("parse_error", "sheet_shape")
        for row_offset, row in enumerate(rows):
            if type(row) is not list or len(row) != width:
                raise ChildFailure("parse_error", "sheet_shape")
            for column_offset, value in enumerate(row):
                rendered = _render_scalar(value)
                if rendered is None:
                    continue
                scalar_type, text = rendered
                label = (
                    f"sheet-{sheet_index}!"
                    f"{_column_label(start_column + column_offset + 1)}"
                    f"{start_row + row_offset + 1}"
                )
                output.add(label, scalar_type, text)
    return (
        output, sheet_count, worksheet_count, skipped_sheet_count,
        dense_cell_count,
    )


def _coordinate(value: object) -> tuple[int, int]:
    if (
        type(value) is not tuple
        or len(value) != 2
        or any(type(part) is not int or part < 0 for part in value)
    ):
        raise ChildFailure("parse_error", "sheet_shape")
    return value


def _render_scalar(value: object) -> tuple[str, str] | None:
    if type(value) is dt.datetime:
        if value.tzinfo is not None:
            raise ChildFailure("parse_error", "scalar_type")
        return "datetime", f"datetime:{value.isoformat(timespec='microseconds')}"
    if type(value) is dt.date:
        return "date", f"date:{value.isoformat()}"
    if type(value) is dt.time:
        if value.tzinfo is not None:
            raise ChildFailure("parse_error", "scalar_type")
        return "time", f"time:{value.isoformat(timespec='microseconds')}"
    if type(value) is dt.timedelta:
        microseconds = (
            (value.days * 86_400 + value.seconds) * 1_000_000
            + value.microseconds
        )
        return "duration", f"duration_us:{microseconds}"
    if type(value) is bool:
        return "bool", "TRUE" if value else "FALSE"
    if type(value) is int:
        if not MIN_CALAMINE_INT <= value <= MAX_CALAMINE_INT:
            raise ChildFailure("parse_error", "scalar_type")
        return "int", str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ChildFailure("parse_error", "scalar_type")
        return "float", repr(value)
    if type(value) is str:
        _safe_text(value)
        return None if not value.strip() else ("string", value)
    raise ChildFailure("parse_error", "scalar_type")


def _safe_text(text: str) -> None:
    if any(
        char == "\x00"
        or ord(char) < 32 and char not in "\t\n\r"
        or 127 <= ord(char) < 160
        for char in text
    ):
        raise ChildFailure("parse_error", "control_character")


def _column_label(number: int) -> str:
    if not 1 <= number <= MAX_XLS_COLUMNS:
        raise ChildFailure("parse_error", "sheet_shape")
    rendered = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        rendered = chr(65 + remainder) + rendered
    return rendered


def _close_quietly(workbook) -> None:
    try:
        workbook.close()
    except BaseException:
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 2
    try:
        max_bytes = _positive(arguments[0])
        max_chars = _positive(arguments[1])
        calamine = _load_calamine()
        if calamine is None:
            return 0
        output, sheets, worksheets, skipped, dense = _extract(
            calamine, max_bytes, max_chars
        )
        _write_frame(
            "success", output=output, sheet_count=sheets,
            worksheet_count=worksheets, skipped_sheet_count=skipped,
            dense_cell_count=dense,
        )
    except ChildFailure as exc:
        _write_frame(exc.status, detail=exc.detail)
    except MemoryError:
        _write_frame("parse_oom", detail="memory_limit")
    except (OSError, ValueError):
        _write_frame("parse_error", detail="calamine_failed")
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
