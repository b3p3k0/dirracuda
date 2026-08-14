"""C4 sandboxed RTF and plain-text extraction contracts."""

from __future__ import annotations

import ast
import codecs
import json
import os
import random
from pathlib import Path

import pytest

from experimental.analyst import extract, parser_child
from experimental.analyst.extract import ExtractionResult, extract_document
from experimental.analyst.formats import SNIFF_BYTES, TextFormat, sniff_text_format
from experimental.analyst.models import FileTerminal
from experimental.analyst.sandbox import SandboxResult, _inventory_for_fd


def _rtf(data: bytes, *, byte_limit: int = 1_000_000,
         char_limit: int = 1_000_000) -> str:
    output = parser_child.Output(byte_limit, char_limit)
    return parser_child.RtfParser(data, output).parse()


def _source(tmp_path: Path, body: bytes):
    path = tmp_path / "public-synthetic.bin"
    path.write_bytes(body)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    return path, fd, _inventory_for_fd(fd)


@pytest.mark.parametrize(
    "body",
    [
        b"%PDF-1.7", b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        b"\x7fELF", b"MZpayload", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff",
        b"GIF89a", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00",
        b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07", b"SQLite format 3\x00",
    ],
)
def test_known_binary_magic_never_routes_to_text(body: bytes) -> None:
    assert sniff_text_format(body) is None


def test_magic_routes_rtf_and_supported_text_encodings() -> None:
    assert sniff_text_format(br"{\rtf1 body}") is TextFormat.RTF
    assert sniff_text_format(b"ordinary UTF-8 \xe2\x80\x94 text") is TextFormat.TEXT
    assert sniff_text_format(codecs.BOM_UTF16_LE + "hello".encode("utf-16-le")) \
        is TextFormat.TEXT
    assert sniff_text_format(b"\x00binary") is None


def test_sniffer_input_is_explicitly_bounded() -> None:
    with pytest.raises(ValueError):
        sniff_text_format(b"x" * (SNIFF_BYTES + 1))
    with pytest.raises(ValueError):
        sniff_text_format(bytearray(b"text"))  # type: ignore[arg-type]


def test_rtf_extracts_textual_controls_and_escaped_literals() -> None:
    body = (br"{\rtf1\ansi one\tab two\par three\line four "
            br"\bullet\~x\_y \{brace\} \\slash}")
    assert _rtf(body) == (
        "one\ttwo\nthree\nfour \u2022\u00a0x\u2011y {brace} \\slash"
    )


def test_rtf_decodes_hex_and_declared_single_byte_codepages() -> None:
    assert _rtf(br"{\rtf1\ansi\ansicpg1252 caf\'e9}") == "caf\u00e9"
    assert _rtf(br"{\rtf1\ansi\ansicpg1251 \'cf\'f0\'e8\'e2\'e5\'f2}") \
        == "\u041f\u0440\u0438\u0432\u0435\u0442"


def test_rtf_tracks_font_specific_multibyte_codepages() -> None:
    body = (
        br"{\rtf1\ansi{\fonttbl{\f0\fcharset0 Arial;}"
        br"{\f1\fcharset128 MS Gothic;}}\f1 \'82\'a0}"
    )
    assert _rtf(body) == "\u3042"
    symbol = br"{\rtf1{\fonttbl{\f2\fcharset2 Symbol;}}\f2 \'41}"
    with pytest.raises(parser_child.ParseFailure, match="unsupported_codepage"):
        _rtf(symbol)


def test_rtf_unicode_fallback_and_surrogate_pair_are_exact() -> None:
    body = br"{\rtf1\ansi\uc1 snow \u9731? face \u-10179?\u-8704?}"
    assert _rtf(body) == "snow \u2603 face \U0001f600"
    assert _rtf(br"{\rtf1\uc0 no-fallback \u9731}") == "no-fallback \u2603"
    assert _rtf(br"{\rtf1\uc1\u9731\emdash retained}") == "\u2603retained"
    with pytest.raises(parser_child.ParseFailure, match="unicode_fallback"):
        _rtf(br"{\rtf1\uc1\u9731}")


def test_rtf_skips_metadata_binary_and_ansi_upr_branch() -> None:
    body = (
        br"{\rtf1\ansi{\fonttbl{\f0 Hidden Font;}}"
        br"{\info{\author Hidden Author}}"
        br"Visible {\*\unknown hidden}"
        br"\bin6 {}\\xx"
        br"{\upr{ANSI duplicate}{\*\ud Unicode only}}}"
    )
    assert _rtf(body) == "Visible Unicode only"


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (br"{\rtf1 missing", "unbalanced_group"),
        (br"{\rtf1 ok}}", "trailing_content"),
        (br"{\rtf1 ok}trailing", "trailing_content"),
        (br"{\rtf1 ok}\par", "trailing_content"),
        (br"{\rtf1\ansicpg932 text}", "unsupported_codepage"),
        (br"{\rtf1\u-10179?}", "unicode_surrogate"),
        (br"{\rtf1\bin999 x}", "binary_length"),
        (b"not rtf", "rtf_header"),
    ],
)
def test_rtf_malformed_inputs_fail_closed(body: bytes, detail: str) -> None:
    with pytest.raises(parser_child.ParseFailure) as raised:
        _rtf(body)
    assert raised.value.detail == detail


def test_rtf_depth_control_word_and_output_limits() -> None:
    too_deep = b"{\\rtf1" + b"{" * parser_child.MAX_GROUP_DEPTH + b"x" + \
        b"}" * (parser_child.MAX_GROUP_DEPTH + 1)
    with pytest.raises(parser_child.ParseFailure, match="group_depth"):
        _rtf(too_deep)
    long_word = b"{\\rtf1\\" + b"a" * (parser_child.MAX_CONTROL_WORD + 1) + b" x}"
    with pytest.raises(parser_child.ParseFailure, match="control_word"):
        _rtf(long_word)
    with pytest.raises(parser_child.OutputLimit, match="text_limit"):
        _rtf(br"{\rtf1 too much text}", byte_limit=4)


def test_seeded_hostile_rtf_bytes_have_only_closed_outcomes() -> None:
    rng = random.Random(20260814)
    for _ in range(1000):
        body = b"{\\rtf1 " + rng.randbytes(rng.randrange(0, 257)) + b"}"
        try:
            text = _rtf(body, byte_limit=4096, char_limit=4096)
        except parser_child.ParseFailure as exc:
            assert exc.detail in extract._CHILD_DETAILS
        else:
            assert "\x00" not in text


@pytest.mark.parametrize(
    ("body", "encoding", "expected"),
    [
        (b"plain UTF-8 \xe2\x80\x94", "utf-8", "plain UTF-8 \u2014"),
        (codecs.BOM_UTF8 + "bom".encode(), "utf-8-bom", "bom"),
        (codecs.BOM_UTF16_LE + "snow \u2603".encode("utf-16-le"),
         "utf-16-le-bom", "snow \u2603"),
        (codecs.BOM_UTF16_BE + "snow \u2603".encode("utf-16-be"),
         "utf-16-be-bom", "snow \u2603"),
        (codecs.BOM_UTF32_LE + "face \U0001f600".encode("utf-32-le"),
         "utf-32-le-bom", "face \U0001f600"),
        (b"caf\xe9", "windows-1252", "caf\u00e9"),
    ],
)
def test_plain_decoder_is_strict_and_deterministic(
    body: bytes, encoding: str, expected: str,
) -> None:
    output = parser_child.Output(1024, 1024)
    text, observed = parser_child.decode_plain(body, output)
    assert (observed, text) == (encoding, expected)


@pytest.mark.parametrize("body", [b"nul\x00byte", b"bad\x01control", b"bad\x81cp"])
def test_plain_decoder_rejects_binary_or_undefined_control_bytes(body: bytes) -> None:
    with pytest.raises(parser_child.ParseFailure):
        parser_child.decode_plain(body, parser_child.Output(1024, 1024))


def test_child_bounded_reader_handles_short_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = iter((b"ab", b"cd", b""))
    monkeypatch.setattr(parser_child.os, "read", lambda fd, size: next(chunks))
    assert parser_child._read_bounded(9, 10) == b"abcd"


def test_live_sandbox_extracts_public_text_and_rtf(tmp_path: Path) -> None:
    cases = [
        (b"public UTF-8 \xe2\x80\x94 text", "text", "public UTF-8 \u2014 text"),
        (br"{\rtf1\ansi Public \b RTF\b0\par snow \u9731?}",
         "rtf", "Public RTF\nsnow \u2603"),
    ]
    for body, format_name, expected_text in cases:
        path = tmp_path / f"sample-{format_name}.bin"
        path.write_bytes(body)
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            result = extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
        finally:
            os.close(fd)
        assert result == ExtractionResult(
            "success", format_name, format_name if format_name == "rtf" else "utf-8",
            expected_text, None,
        )


def test_empty_oversize_and_unsupported_stop_before_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _path, fd, expected = _source(tmp_path, b"%PDF-1.7")
    monkeypatch.setattr(
        extract, "run_sandboxed",
        lambda **kwargs: pytest.fail("rejected source reached sandbox"),
    )
    try:
        assert extract_document(source_fd=fd, expected=expected).reason == \
            FileTerminal.UNSUPPORTED_FORMAT.value
        empty = expected.__class__(
            expected.relative_path, 0, expected.mtime_ns, expected.ctime_ns,
            expected.device, expected.inode, expected.mode, expected.sha256,
        )
        assert extract_document(source_fd=fd, expected=empty).reason == \
            FileTerminal.EMPTY.value
        oversize = expected.__class__(
            expected.relative_path, extract.MAX_SOURCE_BYTES + 1,
            expected.mtime_ns, expected.ctime_ns, expected.device, expected.inode,
            expected.mode, expected.sha256,
        )
        assert extract_document(source_fd=fd, expected=oversize).reason == \
            FileTerminal.OVERSIZE.value
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "reason",
    ["cancelled", "parse_timeout", "parse_signal", "parser_output_limit",
     "source_changed_since_inventory", "sandbox_unavailable", "sandbox_error"],
)
def test_supervisor_failure_reasons_pass_through_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str,
) -> None:
    _path, fd, expected = _source(tmp_path, b"public text")
    monkeypatch.setattr(extract, "python_runtime_binds", lambda: ())
    monkeypatch.setattr(
        extract, "run_sandboxed",
        lambda **kwargs: SandboxResult(reason, 1, b"secret", b"secret", "unit"),
    )
    try:
        result = extract_document(source_fd=fd, expected=expected)
    finally:
        os.close(fd)
    assert result == ExtractionResult(reason)


def test_frame_validator_rejects_partial_or_coerced_payloads() -> None:
    valid_header = {
        "detail": None, "encoding": "utf-8", "format": "text",
        "status": "success", "text_bytes": 2, "text_chars": 2,
    }

    def frame(header: dict, body: bytes = b"ok") -> bytes:
        return extract.FRAME_MAGIC + json.dumps(
            header, sort_keys=True, separators=(",", ":")
        ).encode("ascii") + b"\n" + body

    assert extract._decode_frame(frame(valid_header), TextFormat.TEXT).ok
    for changed, body in [
        ({**valid_header, "text_bytes": True}, b"ok"),
        ({**valid_header, "text_chars": 3}, b"ok"),
        ({**valid_header, "format": "rtf"}, b"ok"),
        ({**valid_header, "extra": 1}, b"ok"),
        ({**valid_header, "status": "parse_error", "detail": "text_decode"}, b"ok"),
        ({**valid_header, "encoding": []}, b"ok"),
        ({**valid_header, "detail": []}, b"ok"),
    ]:
        assert extract._decode_frame(frame(changed, body), TextFormat.TEXT).reason == \
            FileTerminal.PARSE_ERROR.value
    duplicate = (
        extract.FRAME_MAGIC
        + b'{"detail":null,"encoding":"utf-8","format":"text",'
          b'"status":"success","text_bytes":2,"text_bytes":2,"text_chars":2}\n'
        + b"ok"
    )
    assert extract._decode_frame(duplicate, TextFormat.TEXT).reason == \
        FileTerminal.PARSE_ERROR.value


def test_runtime_allowlist_is_narrow_and_child_is_not_imported_by_orchestrator() -> None:
    bindings = extract.python_runtime_binds()
    sources = {binding.source for binding in bindings}
    assert Path("/usr") not in sources
    assert Path.home() not in sources
    assert extract.CHILD_PATH in sources
    assert any(binding.destination == extract.CHILD_DESTINATION for binding in bindings)
    tree = ast.parse(Path(extract.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "parser_child" not in imported


def test_cached_runtime_discovery_still_revalidates_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []
    original = extract._require_trusted_runtime

    def counted(path: Path, *, allow_project: bool = False) -> None:
        calls.append(path)
        original(path, allow_project=allow_project)

    monkeypatch.setattr(extract, "_require_trusted_runtime", counted)
    first = extract.python_runtime_binds()
    first_count = len(calls)
    second = extract.python_runtime_binds()
    assert first == second
    assert first_count >= len(first)
    assert len(calls) == first_count + len(second)
