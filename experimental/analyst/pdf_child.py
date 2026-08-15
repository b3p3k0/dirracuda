"""Standalone PyMuPDF worker executed only inside the C3 sandbox."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

INPUT_PATH = "/input/document"
SITE_PACKAGES = "/runtime/site-packages"
FRAME_MAGIC = b"DIRRACUDA_ANALYST_PDF_V1\n"
PAGE_SEPARATOR = "\f"
PYMUPDF_VERSION = "1.28.0"
MUPDF_VERSION = "1.28.0"


class OutputLimit(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ParseFailure(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class PdfOutput:
    status: str
    detail: str | None = None
    text: str = ""
    page_char_counts: tuple[int, ...] = ()
    text_page_count: int = 0


def _positive(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise ValueError
    value = int(raw)
    if value <= 0:
        raise ValueError
    return value


def _safe_page_text(text: object) -> str:
    if type(text) is not str:
        raise ParseFailure("text_type")
    if any(
        char == "\x00"
        or ord(char) < 32 and char not in "\t\n\r"
        or 127 <= ord(char) < 160
        for char in text
    ):
        raise ParseFailure("control_character")
    return text


def _write_frame(
    status: str,
    *,
    detail: str | None = None,
    text: str = "",
    page_char_counts: tuple[int, ...] = (),
    text_page_count: int = 0,
    pymupdf_version: str | None = PYMUPDF_VERSION,
    mupdf_version: str | None = MUPDF_VERSION,
) -> None:
    body = text.encode("utf-8", errors="strict")
    header = {
        "detail": detail,
        "format": "pdf",
        "mupdf_version": mupdf_version,
        "page_char_counts": page_char_counts,
        "page_count": len(page_char_counts),
        "pymupdf_version": pymupdf_version,
        "status": status,
        "text_bytes": len(body),
        "text_chars": len(text),
        "text_page_count": text_page_count,
    }
    encoded = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    sys.stdout.buffer.write(FRAME_MAGIC + encoded + b"\n" + body)
    sys.stdout.buffer.flush()


def _load_pymupdf():
    # PyMuPDF defaults diagnostics to stdout, which would corrupt the frame.
    os.environ["PYMUPDF_MESSAGE"] = "fd:2"
    if SITE_PACKAGES not in sys.path:
        sys.path.insert(0, SITE_PACKAGES)
    try:
        import pymupdf  # type: ignore[import-not-found]
    except Exception:
        _write_frame(
            "dependency_unavailable",
            detail="dependency_missing",
            pymupdf_version=None,
            mupdf_version=None,
        )
        return None
    observed_python = getattr(pymupdf, "pymupdf_version", None)
    observed_mupdf = getattr(pymupdf, "mupdf_version", None)
    if observed_python != PYMUPDF_VERSION or observed_mupdf != MUPDF_VERSION:
        _write_frame(
            "dependency_unavailable",
            detail="dependency_version",
            pymupdf_version=(observed_python if type(observed_python) is str else None),
            mupdf_version=(observed_mupdf if type(observed_mupdf) is str else None),
        )
        return None
    return pymupdf


def _extract(pymupdf, max_pages: int, max_bytes: int, max_chars: int) -> PdfOutput:
    document = pymupdf.open(filename=INPUT_PATH, filetype="pdf")
    try:
        if document.is_pdf is not True:
            raise ParseFailure("format_mismatch")
        needs_pass = document.needs_pass
        if (type(needs_pass) not in (bool, int)
                or needs_pass not in (False, True, 0, 1)):
            raise ParseFailure("encryption_state")
        if bool(needs_pass):
            return PdfOutput("encrypted", "password_required")
        page_count = document.page_count
        if type(page_count) is not int or page_count < 0:
            raise ParseFailure("page_count")
        if page_count > max_pages:
            return PdfOutput("parser_output_limit", "page_limit")

        pages: list[str] = []
        page_counts: list[int] = []
        text_page_count = 0
        byte_count = 0
        char_count = 0
        for page_number in range(page_count):
            page = document.load_page(page_number)
            text = _safe_page_text(page.get_text("text", sort=True))
            separator_chars = 1 if page_number else 0
            separator_bytes = 1 if page_number else 0
            encoded_length = len(text.encode("utf-8", errors="strict"))
            if (
                byte_count + separator_bytes + encoded_length > max_bytes
                or char_count + separator_chars + len(text) > max_chars
            ):
                raise OutputLimit("text_limit")
            pages.append(text)
            page_counts.append(len(text))
            byte_count += separator_bytes + encoded_length
            char_count += separator_chars + len(text)
            if text.strip():
                text_page_count += 1
        counts = tuple(page_counts)
        if text_page_count == 0:
            return PdfOutput(
                "no_text_layer", "no_text_layer",
                page_char_counts=(0,) * page_count,
            )
        return PdfOutput(
            "success",
            text=PAGE_SEPARATOR.join(pages),
            page_char_counts=counts,
            text_page_count=text_page_count,
        )
    finally:
        document.close()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 64
    try:
        max_pages, max_bytes, max_chars = map(_positive, argv)
    except (TypeError, ValueError):
        return 64
    pymupdf = _load_pymupdf()
    if pymupdf is None:
        return 0
    try:
        output = _extract(pymupdf, max_pages, max_bytes, max_chars)
        _write_frame(
            output.status,
            detail=output.detail,
            text=output.text,
            page_char_counts=output.page_char_counts,
            text_page_count=output.text_page_count,
        )
    except OutputLimit as exc:
        _write_frame("parser_output_limit", detail=exc.detail)
    except ParseFailure as exc:
        _write_frame("parse_error", detail=exc.detail)
    except MemoryError:
        _write_frame("parse_oom", detail="memory_limit")
    except Exception:
        _write_frame("parse_error", detail="pdf_parse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
