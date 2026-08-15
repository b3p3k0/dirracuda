"""Pure bounded magic sniffing for Analyst document routing."""

from __future__ import annotations

import codecs
from enum import Enum
from typing import Final

SNIFF_BYTES: Final = 4096


class DocumentFormat(str, Enum):
    PDF = "pdf"
    RTF = "rtf"
    TEXT = "text"


# Compatibility name retained for C4 callers.  PDF is intentionally returned
# only by sniff_document_format(), never by the legacy text-only sniffer.
TextFormat = DocumentFormat


_BINARY_SIGNATURES: Final = (
    b"%PDF-",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"\x7fELF",
    b"MZ",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"SQLite format 3\x00",
)
_TEXT_BOMS: Final = (
    codecs.BOM_UTF8,
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
)


def sniff_text_format(head: bytes) -> TextFormat | None:
    """Classify a bounded prefix without trusting a filename extension."""
    if type(head) is not bytes or len(head) > SNIFF_BYTES:
        raise ValueError("format sniff requires at most 4096 bytes")
    if head.startswith(b"{\\rtf"):
        return TextFormat.RTF
    if any(head.startswith(signature) for signature in _BINARY_SIGNATURES):
        return None
    if any(head.startswith(bom) for bom in _TEXT_BOMS):
        return TextFormat.TEXT
    if b"\x00" in head:
        return None
    if not head:
        return TextFormat.TEXT
    for encoding in ("utf-8", "cp1252"):
        try:
            decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
            text = decoder.decode(head, final=False)
        except UnicodeError:
            continue
        if _looks_textual(text):
            return TextFormat.TEXT
    return None


def sniff_document_format(head: bytes) -> DocumentFormat | None:
    """Classify every currently supported Analyst document family."""
    if type(head) is not bytes or len(head) > SNIFF_BYTES:
        raise ValueError("format sniff requires at most 4096 bytes")
    if head.startswith(b"%PDF-"):
        return DocumentFormat.PDF
    return sniff_text_format(head)


def _looks_textual(text: str) -> bool:
    if not text:
        return True
    forbidden = sum(
        ord(char) < 32 and char not in "\t\n\r\f" or 127 <= ord(char) < 160
        for char in text
    )
    return forbidden * 100 <= len(text)
