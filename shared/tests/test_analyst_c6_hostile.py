"""Hostile ZIP, relationship and XML cases for C6 OOXML extraction."""

from __future__ import annotations

import builtins
import io
import os
import stat
import struct
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from experimental.analyst import ooxml_child
from experimental.analyst.ooxml_frame import decode_ooxml_frame
from experimental.analyst.models import FileTerminal
from experimental.analyst.ooxml_contract import (
    MAX_COMPRESSION_RATIO,
    MAX_EXPANDED_BYTES,
    MAX_LOGICAL_UNITS,
    MAX_MEMBER_BYTES,
    MAX_XML_MEMBER_BYTES,
    MAX_XML_PACKAGE_BYTES,
)
from shared.tests.test_analyst_c6 import (
    MAIN_PARTS,
    OFFICE_RELS,
    _extract_path,
    _package,
    _relationships,
    _xlsx,
)


def _info(
    name: str,
    *,
    size: int = 0,
    compressed: int | None = None,
    flags: int = 0,
    method: int = zipfile.ZIP_STORED,
    mode: int = 0,
    directory: bool = False,
):
    return SimpleNamespace(
        filename=name,
        flag_bits=flags,
        compress_type=method,
        file_size=size,
        compress_size=size if compressed is None else compressed,
        external_attr=mode << 16,
        is_dir=lambda: directory,
    )


class _Archive:
    def __init__(self, *infos) -> None:
        self.infos = list(infos)

    def infolist(self):
        return self.infos


@pytest.mark.parametrize(
    "name",
    [
        "/absolute.xml", "../escape.xml", "part/../escape.xml",
        "part/./dot.xml", "part//double.xml", "C:drive.xml", "part\\windows.xml",
        "part/%2e%2e/escape.xml", "part/%2Fescape.xml", "part/\x00bad.xml",
    ],
)
def test_member_path_variants_fail_before_decompression(name: str) -> None:
    with pytest.raises(ooxml_child.ParseFailure, match="archive_path"):
        ooxml_child._inspect_archive(_Archive(_info(name)))


def test_duplicate_casefold_symlink_special_and_encryption_are_rejected() -> None:
    with pytest.raises(ooxml_child.ParseFailure, match="archive_duplicate"):
        ooxml_child._inspect_archive(_Archive(
            _info("word/document.xml"), _info("WORD/DOCUMENT.XML")
        ))
    for mode in (stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o600):
        with pytest.raises(ooxml_child.ParseFailure, match="archive_path"):
            ooxml_child._inspect_archive(_Archive(_info("part.xml", mode=mode)))
    with pytest.raises(ooxml_child.ParseFailure, match="archive_encrypted"):
        ooxml_child._inspect_archive(_Archive(_info("part.xml", flags=1)))


def test_archive_limits_accept_exact_boundaries_and_reject_cap_plus_one() -> None:
    exact_member = _Archive(_info(
        "large.bin", size=MAX_MEMBER_BYTES, compressed=MAX_MEMBER_BYTES,
    ))
    members, expanded = ooxml_child._inspect_archive(exact_member)
    assert len(members) == 1 and expanded == MAX_MEMBER_BYTES
    with pytest.raises(ooxml_child.OutputLimit, match="member_size"):
        ooxml_child._inspect_archive(_Archive(_info(
            "too-large.bin", size=MAX_MEMBER_BYTES + 1,
            compressed=MAX_MEMBER_BYTES + 1,
        )))

    exact_expanded = _Archive(
        _info("one.bin", size=MAX_MEMBER_BYTES, compressed=MAX_MEMBER_BYTES),
        _info("two.bin", size=MAX_MEMBER_BYTES, compressed=MAX_MEMBER_BYTES),
    )
    assert ooxml_child._inspect_archive(exact_expanded)[1] == MAX_EXPANDED_BYTES
    with pytest.raises(ooxml_child.OutputLimit, match="expanded_limit"):
        ooxml_child._inspect_archive(_Archive(
            *exact_expanded.infos, _info("plus-one.bin", size=1, compressed=1)
        ))

    exact_ratio = _info(
        "ratio.xml", size=MAX_COMPRESSION_RATIO * 100, compressed=100,
        method=zipfile.ZIP_DEFLATED,
    )
    ooxml_child._inspect_archive(_Archive(exact_ratio))
    with pytest.raises(ooxml_child.OutputLimit, match="member_ratio"):
        ooxml_child._inspect_archive(_Archive(_info(
            "ratio.xml", size=MAX_COMPRESSION_RATIO * 100 + 1,
            compressed=100, method=zipfile.ZIP_DEFLATED,
        )))


def test_selected_xml_byte_limits_accept_exact_and_reject_plus_one() -> None:
    class Archive:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def open(self, _info, _mode):
            return io.BytesIO(self.body)

    class Iterator:
        root = SimpleNamespace()

        def __iter__(self):
            return iter(())

    parser = SimpleNamespace(iterparse=lambda *_args, **_kwargs: Iterator())

    exact = _info("part.xml", size=MAX_XML_MEMBER_BYTES)
    budget = ooxml_child.XmlBudget()
    assert ooxml_child._read_tree(
        Archive(b"x" * MAX_XML_MEMBER_BYTES), {"part.xml": exact},
        "part.xml", parser, budget,
    ) is Iterator.root
    assert budget.bytes_read == MAX_XML_MEMBER_BYTES

    too_large = _info("part.xml", size=MAX_XML_MEMBER_BYTES + 1)
    with pytest.raises(ooxml_child.OutputLimit, match="xml_size"):
        ooxml_child._read_tree(
            Archive(b""), {"part.xml": too_large}, "part.xml", parser,
            ooxml_child.XmlBudget(),
        )

    one_byte = _info("part.xml", size=1)
    exact_package = ooxml_child.XmlBudget(
        bytes_read=MAX_XML_PACKAGE_BYTES - 1,
    )
    ooxml_child._read_tree(
        Archive(b"x"), {"part.xml": one_byte}, "part.xml", parser,
        exact_package,
    )
    assert exact_package.bytes_read == MAX_XML_PACKAGE_BYTES
    with pytest.raises(ooxml_child.OutputLimit, match="xml_package_limit"):
        ooxml_child._read_tree(
            Archive(b"x"), {"part.xml": one_byte}, "part.xml", parser,
            ooxml_child.XmlBudget(bytes_read=MAX_XML_PACKAGE_BYTES),
        )

def test_unsupported_compression_is_closed_without_decompression(tmp_path: Path) -> None:
    path = tmp_path / "bzip.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_BZIP2) as archive:
        archive.writestr("public.txt", "public")
    result = _extract_path(path)
    assert (result.reason, result.detail) == (
        FileTerminal.UNSUPPORTED_FORMAT.value, "compression_method"
    )


def test_semantic_unit_limit_accepts_exact_and_rejects_plus_one() -> None:
    output = ooxml_child.Output(MAX_LOGICAL_UNITS * 2, MAX_LOGICAL_UNITS * 2)
    for index in range(MAX_LOGICAL_UNITS):
        output.add("paragraph", f"main#p{index + 1}", "x")
    assert output.logical_unit_count == MAX_LOGICAL_UNITS
    with pytest.raises(ooxml_child.OutputLimit, match="semantic_unit_limit"):
        output.add("paragraph", f"main#p{MAX_LOGICAL_UNITS + 1}", "x")


def test_child_replaces_an_oversized_success_header_with_one_failure_frame(
    monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    output = ooxml_child.Output(10, 10)
    output.add("paragraph", "main#p1", "x")
    output.primary_unit_count = 1
    monkeypatch.setattr(ooxml_child, "MAX_HEADER_BYTES", 240)

    ooxml_child._write_frame("success", format_name="docx", output=output)

    decoded = decode_ooxml_frame(
        capfdbinary.readouterr().out, max_text_bytes=10, max_text_chars=10,
    )
    assert (decoded.reason, decoded.detail) == (
        FileTerminal.PARSER_OUTPUT_LIMIT.value, "semantic_unit_limit",
    )


def test_root_and_required_external_relationships_are_never_followed(
    tmp_path: Path,
) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    main = f'<w:document xmlns:w="{word}"><w:body/></w:document>'
    # Rebuild root relationship as external; no network is contacted.
    rebuilt = tmp_path / "external-root-rebuilt.docx"
    main_part = MAIN_PARTS["docx"]
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/{main_part}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(rebuilt, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", _relationships([(
            "rId1", f"{OFFICE_RELS}/officeDocument",
            "https://example.invalid/document.xml", "External",
        )]))
        archive.writestr(main_part, main)
    result = _extract_path(rebuilt)
    assert (result.reason, result.detail) == (
        FileTerminal.PARSE_ERROR.value, "main_relationship"
    )

    xlsx = tmp_path / "external-sheet.xlsx"
    _xlsx(xlsx)
    # Replacing equal-length XML preserves the rest of the synthetic package.
    with zipfile.ZipFile(xlsx, "r") as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    entries["xl/_rels/workbook.xml.rels"] = _relationships([
        ("rId1", f"{OFFICE_RELS}/worksheet", "https://example.invalid/sheet.xml", "External"),
        ("rId2", f"{OFFICE_RELS}/sharedStrings", "sharedStrings.xml", None),
    ]).encode()
    with zipfile.ZipFile(xlsx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    assert _extract_path(xlsx).detail == "relationship"


def test_irrelevant_external_hyperlink_is_not_fetched_or_treated_as_text(
    tmp_path: Path,
) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    main = f'<w:document xmlns:w="{word}"><w:body><w:p><w:r><w:t>Public</w:t></w:r></w:p></w:body></w:document>'
    rels = _relationships([(
        "rIdLink", f"{OFFICE_RELS}/hyperlink", "https://example.invalid/", "External"
    )])
    path = tmp_path / "external-link.docx"
    _package(path, "docx", main, parts={"word/_rels/document.xml.rels": rels})
    result = _extract_path(path)
    assert result.ok and result.text == "Public"


def test_encoded_relationship_escape_and_bad_shared_index_fail_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "encoded.xlsx"
    _xlsx(path)
    with zipfile.ZipFile(path, "r") as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    entries["xl/_rels/workbook.xml.rels"] = _relationships([
        ("rId1", f"{OFFICE_RELS}/worksheet", "%2e%2e/escape.xml", None),
        ("rId2", f"{OFFICE_RELS}/sharedStrings", "sharedStrings.xml", None),
    ]).encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    assert _extract_path(path).detail == "relationship"

    bad_index = tmp_path / "bad-index.xlsx"
    _xlsx(bad_index)
    with zipfile.ZipFile(bad_index, "r") as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    entries["xl/worksheets/sheet1.xml"] = entries[
        "xl/worksheets/sheet1.xml"
    ].replace(b"<v>0</v>", b"<v>999</v>", 1)
    with zipfile.ZipFile(bad_index, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    assert _extract_path(bad_index).detail == "shared_string"


def test_sparse_cell_outside_real_excel_grid_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "extreme.xlsx"
    _xlsx(path)
    with zipfile.ZipFile(path, "r") as source:
        entries = {item.filename: source.read(item) for item in source.infolist()}
    entries["xl/worksheets/sheet1.xml"] = entries[
        "xl/worksheets/sheet1.xml"
    ].replace(b'r="A1"', b'r="XFE1048577"', 1)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    assert _extract_path(path).detail == "cell_reference"


def test_xml_depth_and_attribute_floods_fail_closed(tmp_path: Path) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    deep = "".join(f'<w:x{i}>' for i in range(129))
    deep += "".join(f'</w:x{i}>' for i in reversed(range(129)))
    path = tmp_path / "deep.docx"
    _package(
        path,
        "docx",
        f'<w:document xmlns:w="{word}">{deep}</w:document>',
    )
    result = _extract_path(path)
    assert (result.reason, result.detail) == (
        FileTerminal.PARSER_OUTPUT_LIMIT.value, "xml_depth"
    )

    attributes = " ".join(f'a{i}="x"' for i in range(65))
    path = tmp_path / "attributes.docx"
    _package(
        path,
        "docx",
        f'<w:document xmlns:w="{word}" {attributes}><w:body/></w:document>',
    )
    assert _extract_path(path).detail == "attribute_limit"


def test_crc_corruption_discards_all_partial_text(tmp_path: Path) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    path = tmp_path / "crc.docx"
    _package(
        path,
        "docx",
        f'<w:document xmlns:w="{word}"><w:body><w:p><w:r><w:t>CRC TEXT</w:t></w:r></w:p></w:body></w:document>',
    )
    with zipfile.ZipFile(path, "r") as archive:
        info = archive.getinfo("word/document.xml")
        offset = info.header_offset
    body = bytearray(path.read_bytes())
    name_len, extra_len = struct.unpack_from("<HH", body, offset + 26)
    data_offset = offset + 30 + name_len + extra_len
    body[data_offset + max(0, info.compress_size // 2)] ^= 0x01
    path.write_bytes(body)
    result = _extract_path(path)
    assert result.reason == FileTerminal.PARSE_ERROR.value
    assert result.detail == "archive_corrupt"
    assert result.text is None


def test_child_version_mismatch_emits_one_closed_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__
    fake = SimpleNamespace(__version__="0.7.0", ElementTree=SimpleNamespace())

    def controlled_import(name, *args, **kwargs):
        if name == "defusedxml":
            return fake
        return original_import(name, *args, **kwargs)

    frames: list[tuple[str, dict]] = []
    monkeypatch.setattr(builtins, "__import__", controlled_import)
    monkeypatch.setattr(
        ooxml_child,
        "_write_frame",
        lambda status, **values: frames.append((status, values)),
    )
    assert ooxml_child._load_xml_parser() is None
    assert frames == [(
        "dependency_unavailable",
        {"detail": "dependency_version", "defusedxml_version": "0.7.0"},
    )]
