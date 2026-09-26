"""Standalone RTF/plain-text parser executed only inside the C3 sandbox."""

from __future__ import annotations

import codecs
import json
import os
import sys
from dataclasses import dataclass

INPUT_PATH = "/input/document"
FRAME_MAGIC = b"DIRRACUDA_ANALYST_TEXT_V1\n"
MAX_GROUP_DEPTH = 256
MAX_CONTROL_WORD = 64
MAX_PARAMETER = 16
SUPPORTED_CODE_PAGES = {
    437: "cp437", 850: "cp850", 874: "cp874",
    1250: "cp1250", 1251: "cp1251", 1252: "cp1252",
    1253: "cp1253", 1254: "cp1254", 1255: "cp1255",
    1256: "cp1256", 1257: "cp1257", 1258: "cp1258",
    65001: "utf-8",
}
FONT_CHARSETS = {
    0: None, 1: None, 2: "", 77: "mac_roman", 128: "cp932",
    129: "cp949", 130: "cp1361", 134: "gbk", 136: "big5",
    161: "cp1253", 162: "cp1254", 163: "cp1258", 177: "cp1255",
    178: "cp1256", 186: "cp1257", 204: "cp1251", 222: "cp874",
    238: "cp1250", 254: "cp437", 255: "cp850",
}
DESTINATIONS = {
    "annotation", "atnauthor", "atndate", "atnicn", "atnid", "atnparent",
    "atnref", "atntime", "atrfend", "atrfstart", "author", "background",
    "bkmkend", "bkmkstart", "blipuid", "buptim", "category", "colorschememapping",
    "colortbl", "comment", "company", "creatim", "datafield", "datastore",
    "defchp", "defpap", "do", "doccomm", "docvar", "dptxbxtext", "ebcend",
    "ebcstart", "factoidname", "falt", "fchars", "ffdeftext", "ffentrymcr",
    "ffexitmcr", "ffformat", "ffhelptext", "ffl", "ffname", "ffstattext",
    "fieldtype", "file", "filetbl", "fldinst", "fname", "fontemb", "fontfile",
    "fonttbl", "footer", "footerf", "footerl", "footerr", "footnote", "formfield",
    "ftncn", "ftnsep", "ftnsepc", "generator", "header", "headerf", "headerl",
    "headerr", "hl", "hlfr", "hlinkbase", "htmltag", "info", "keycode",
    "keywords", "latentstyles", "lchars", "levelnumbers", "leveltext", "lfolevel",
    "linkval", "list", "listlevel", "listname", "listoverride", "listoverridetable",
    "listpicture", "liststylename", "listtable", "manager", "mhtmltag", "mmath",
    "mmathpr", "nesttableprops", "nextfile", "nonesttables", "objalias", "objclass",
    "objdata", "object", "objname", "objsect", "objtime", "operator", "panose",
    "password", "passwordhash", "pgp", "pgptbl", "picprop", "pict", "pn",
    "pnseclvl", "pntext", "printim", "private", "propname", "protend", "protstart",
    "protusertbl", "pxe", "result", "revtbl", "revtim", "rsidtbl", "rxe",
    "shp", "shpgrp", "shpinst", "shppict", "shprslt", "shptxt", "sn", "sp",
    "staticval", "stylesheet", "subject", "sv", "tc", "template", "themedata",
    "title", "txe", "upr", "userprops", "wgrffmtfilter", "windowcaption",
    "writereservation", "writereservhash", "xe", "xform", "xmlattrname",
    "xmlattrvalue", "xmlclose", "xmlname", "xmlnstbl", "xmlopen",
}
TEXT_CONTROLS = {
    "bullet": "\u2022", "cell": "\t", "emdash": "\u2014",
    "emspace": "\u2003", "endash": "\u2013", "enspace": "\u2002",
    "ldblquote": "\u201c", "line": "\n", "lquote": "\u2018",
    "page": "\n", "par": "\n", "qmspace": "\u2005",
    "rdblquote": "\u201d", "rquote": "\u2019", "row": "\n", "tab": "\t",
}


class ParseFailure(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class OutputLimit(ParseFailure):
    pass


@dataclass(slots=True)
class State:
    ignorable: bool = False
    uc_skip: int = 1
    encoding: str = "cp1252"
    destination: str | None = None
    font_id: int | None = None

    def copy(self) -> "State":
        return State(
            self.ignorable, self.uc_skip, self.encoding,
            self.destination, self.font_id,
        )


class Output:
    def __init__(self, max_bytes: int, max_chars: int) -> None:
        self.parts: list[str] = []
        self.byte_count = 0
        self.char_count = 0
        self.max_bytes = max_bytes
        self.max_chars = max_chars

    def append(self, text: str) -> None:
        if not text:
            return
        if any(char == "\x00" or ord(char) < 32 and char not in "\t\n\r\f"
               or 127 <= ord(char) < 160 for char in text):
            raise ParseFailure("control_character")
        encoded = text.encode("utf-8", errors="strict")
        if (self.byte_count + len(encoded) > self.max_bytes
                or self.char_count + len(text) > self.max_chars):
            raise OutputLimit("text_limit")
        self.parts.append(text)
        self.byte_count += len(encoded)
        self.char_count += len(text)

    def finish(self) -> str:
        return "".join(self.parts)


class RtfParser:
    def __init__(self, data: bytes, output: Output) -> None:
        self.data = data
        self.output = output
        self.state = State()
        self.stack: list[State] = []
        self.pending = bytearray()
        self.pending_high_surrogate: int | None = None
        self.fallback_remaining = 0
        self.root_closed = False
        self.default_encoding = "cp1252"
        self.font_encodings: dict[int, str | None] = {}

    def parse(self) -> str:
        if not self.data.startswith(b"{\\rtf"):
            raise ParseFailure("rtf_header")
        index = 0
        while index < len(self.data):
            value = self.data[index]
            if self.root_closed:
                if value in (0x09, 0x0A, 0x0D, 0x20):
                    index += 1
                    continue
                raise ParseFailure("trailing_content")
            if value == 0x7B:  # {
                self._require_fallback_complete()
                self._flush_bytes()
                if len(self.stack) >= MAX_GROUP_DEPTH:
                    raise ParseFailure("group_depth")
                self.stack.append(self.state.copy())
                index += 1
            elif value == 0x7D:  # }
                self._require_fallback_complete()
                self._flush_bytes()
                if not self.stack:
                    raise ParseFailure("unbalanced_group")
                self.state = self.stack.pop()
                if not self.stack:
                    self.root_closed = True
                index += 1
            elif value == 0x5C:  # backslash
                index = self._control(index + 1)
            elif value in (0x0A, 0x0D):
                self._flush_bytes()
                index += 1
            else:
                if self.fallback_remaining:
                    self.fallback_remaining -= 1
                elif not self.state.ignorable:
                    self.pending.append(value)
                index += 1
        self._flush_bytes()
        self._require_fallback_complete()
        if self.stack:
            raise ParseFailure("unbalanced_group")
        if self.pending_high_surrogate is not None:
            raise ParseFailure("unicode_surrogate")
        return self.output.finish()

    def _control(self, index: int) -> int:
        if index >= len(self.data):
            raise ParseFailure("trailing_escape")
        value = self.data[index]
        if value in b"{}\\":
            if self.fallback_remaining:
                self.fallback_remaining -= 1
            elif not self.state.ignorable:
                self.pending.append(value)
            return index + 1
        if value == 0x27:  # apostrophe hex escape
            if index + 2 >= len(self.data):
                raise ParseFailure("hex_escape")
            raw = self.data[index + 1:index + 3]
            try:
                decoded = int(raw, 16)
            except ValueError as exc:
                raise ParseFailure("hex_escape") from exc
            if self.fallback_remaining:
                self.fallback_remaining -= 1
            elif not self.state.ignorable:
                self.pending.append(decoded)
            return index + 3
        if not _ascii_letter(value):
            self._flush_bytes()
            symbol = chr(value)
            if symbol == "*":
                self.state.ignorable = True
            elif self.fallback_remaining:
                self.fallback_remaining -= 1
            elif not self.state.ignorable:
                self.output.append({"~": "\u00a0", "_": "\u2011", "-": ""}.get(
                    symbol, ""
                ))
            return index + 1

        start = index
        while index < len(self.data) and _ascii_letter(self.data[index]):
            index += 1
            if index - start > MAX_CONTROL_WORD:
                raise ParseFailure("control_word")
        word = self.data[start:index].decode("ascii").lower()
        parameter: int | None = None
        param_start = index
        if index < len(self.data) and self.data[index] == 0x2D:
            index += 1
        digit_start = index
        while index < len(self.data) and 0x30 <= self.data[index] <= 0x39:
            index += 1
            if index - param_start > MAX_PARAMETER:
                raise ParseFailure("control_parameter")
        if index > digit_start:
            try:
                parameter = int(self.data[param_start:index].decode("ascii"))
            except ValueError as exc:
                raise ParseFailure("control_parameter") from exc
        elif index != param_start:
            raise ParseFailure("control_parameter")
        if index < len(self.data) and self.data[index] == 0x20:
            index += 1
        self._flush_bytes()
        if word == "bin":
            self._require_fallback_complete()
            if parameter is None or parameter < 0 or index + parameter > len(self.data):
                raise ParseFailure("binary_length")
            return index + parameter
        self._apply_word(word, parameter)
        return index

    def _apply_word(self, word: str, parameter: int | None) -> None:
        if self.fallback_remaining:
            if word in TEXT_CONTROLS:
                self.fallback_remaining -= 1
                return
            raise ParseFailure("unicode_fallback")
        if word == "ud":
            self.state.ignorable = False
            return
        if word in DESTINATIONS:
            self.state.ignorable = True
            self.state.destination = word
            return
        if word == "f":
            if parameter is None or parameter < 0:
                raise ParseFailure("font_id")
            self.state.font_id = parameter
            if self.state.destination != "fonttbl":
                if parameter in self.font_encodings:
                    encoding = self.font_encodings[parameter]
                    if encoding is None:
                        encoding = self.default_encoding
                else:
                    encoding = self.default_encoding
                if not encoding:
                    raise ParseFailure("unsupported_codepage")
                self.state.encoding = encoding
            return
        if word == "fcharset":
            if (self.state.destination != "fonttbl" or self.state.font_id is None
                    or parameter not in FONT_CHARSETS):
                raise ParseFailure("unsupported_codepage")
            self.font_encodings[self.state.font_id] = FONT_CHARSETS[parameter]
            return
        if word == "cpg" and self.state.destination == "fonttbl":
            if self.state.font_id is None or parameter not in SUPPORTED_CODE_PAGES:
                raise ParseFailure("unsupported_codepage")
            self.font_encodings[self.state.font_id] = SUPPORTED_CODE_PAGES[parameter]
            return
        if word == "uc":
            if parameter is None or not 0 <= parameter <= 16:
                raise ParseFailure("unicode_fallback")
            self.state.uc_skip = parameter
            return
        if word == "u":
            if parameter is None or not -32768 <= parameter <= 65535:
                raise ParseFailure("unicode_value")
            if not self.state.ignorable:
                self._unicode_unit(parameter & 0xFFFF)
            self.fallback_remaining = self.state.uc_skip
            return
        if word == "ansicpg":
            if parameter not in SUPPORTED_CODE_PAGES:
                raise ParseFailure("unsupported_codepage")
            self.state.encoding = SUPPORTED_CODE_PAGES[parameter]
            if len(self.stack) == 1:
                self.default_encoding = self.state.encoding
            return
        if word in {"ansi", "fromtext"}:
            self.state.encoding = "cp1252"
            if len(self.stack) == 1:
                self.default_encoding = self.state.encoding
            return
        if word == "mac":
            self.state.encoding = "mac_roman"
            if len(self.stack) == 1:
                self.default_encoding = self.state.encoding
            return
        if word == "pc":
            self.state.encoding = "cp437"
            if len(self.stack) == 1:
                self.default_encoding = self.state.encoding
            return
        if word == "pca":
            self.state.encoding = "cp850"
            if len(self.stack) == 1:
                self.default_encoding = self.state.encoding
            return
        if word in TEXT_CONTROLS and not self.state.ignorable:
            self._append_text(TEXT_CONTROLS[word])

    def _flush_bytes(self) -> None:
        if not self.pending:
            return
        try:
            text = bytes(self.pending).decode(self.state.encoding, errors="strict")
        except (LookupError, UnicodeError) as exc:
            raise ParseFailure("text_decode") from exc
        self.pending.clear()
        if not self.state.ignorable:
            self._append_text(text)

    def _append_text(self, text: str) -> None:
        if self.pending_high_surrogate is not None:
            raise ParseFailure("unicode_surrogate")
        self.output.append(text)

    def _unicode_unit(self, unit: int) -> None:
        if 0xD800 <= unit <= 0xDBFF:
            if self.pending_high_surrogate is not None:
                raise ParseFailure("unicode_surrogate")
            self.pending_high_surrogate = unit
            return
        if 0xDC00 <= unit <= 0xDFFF:
            if self.pending_high_surrogate is None:
                raise ParseFailure("unicode_surrogate")
            high = self.pending_high_surrogate
            self.pending_high_surrogate = None
            codepoint = 0x10000 + ((high - 0xD800) << 10) + (unit - 0xDC00)
            self.output.append(chr(codepoint))
            return
        if self.pending_high_surrogate is not None:
            raise ParseFailure("unicode_surrogate")
        self.output.append(chr(unit))

    def _require_fallback_complete(self) -> None:
        if self.fallback_remaining:
            raise ParseFailure("unicode_fallback")


def decode_plain(data: bytes, output: Output) -> tuple[str, str]:
    encodings: list[tuple[bytes, str, str]] = [
        (codecs.BOM_UTF32_LE, "utf-32", "utf-32-le-bom"),
        (codecs.BOM_UTF32_BE, "utf-32", "utf-32-be-bom"),
        (codecs.BOM_UTF8, "utf-8-sig", "utf-8-bom"),
        (codecs.BOM_UTF16_LE, "utf-16", "utf-16-le-bom"),
        (codecs.BOM_UTF16_BE, "utf-16", "utf-16-be-bom"),
    ]
    selected: tuple[str, str] | None = None
    text: str | None = None
    for bom, codec, label in encodings:
        if data.startswith(bom):
            selected = codec, label
            break
    if selected is None:
        try:
            text = data.decode("utf-8", errors="strict")
            selected = "utf-8", "utf-8"
        except UnicodeError:
            selected = "cp1252", "windows-1252"
    try:
        if text is None:
            text = data.decode(selected[0], errors="strict")
    except UnicodeError as exc:
        raise ParseFailure("text_decode") from exc
    output.append(text)
    return output.finish(), selected[1]


def extract(data: bytes, format_name: str, max_text_bytes: int,
            max_text_chars: int) -> tuple[str, str]:
    output = Output(max_text_bytes, max_text_chars)
    if format_name == "rtf":
        return RtfParser(data, output).parse(), "rtf"
    if format_name == "text":
        return decode_plain(data, output)
    raise ParseFailure("unsupported_format")


def _ascii_letter(value: int) -> bool:
    return 0x41 <= value <= 0x5A or 0x61 <= value <= 0x7A


def _positive(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise ValueError
    value = int(raw)
    if value <= 0:
        raise ValueError
    return value


def _write_frame(status: str, format_name: str, encoding: str | None,
                 text: str, detail: str | None = None) -> None:
    body = text.encode("utf-8")
    header = {
        "detail": detail,
        "encoding": encoding,
        "format": format_name,
        "status": status,
        "text_bytes": len(body),
        "text_chars": len(text),
    }
    encoded_header = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    sys.stdout.buffer.write(FRAME_MAGIC + encoded_header + b"\n" + body)
    sys.stdout.buffer.flush()


def _read_bounded(fd: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(fd, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        return 64
    try:
        format_name = argv[0]
        max_source_bytes = _positive(argv[1])
        max_text_bytes = _positive(argv[2])
        max_text_chars = _positive(argv[3])
    except (TypeError, ValueError):
        return 64
    try:
        fd = os.open(INPUT_PATH, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            data = _read_bounded(fd, max_source_bytes)
            if len(data) > max_source_bytes:
                _write_frame("oversize", format_name, None, "", "source_limit")
                return 0
        finally:
            os.close(fd)
        text, encoding = extract(data, format_name, max_text_bytes, max_text_chars)
        _write_frame("success", format_name, encoding, text)
    except OutputLimit as exc:
        _write_frame("parser_output_limit", format_name, None, "", exc.detail)
    except ParseFailure as exc:
        _write_frame("parse_error", format_name, None, "", exc.detail)
    except MemoryError:
        _write_frame("parse_oom", format_name, None, "", "memory_limit")
    except OSError:
        _write_frame("parse_error", format_name, None, "", "input_io")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
