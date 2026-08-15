"""C6 optional, sandboxed OOXML extraction contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from experimental.analyst import extract, ooxml_child
from experimental.analyst.extract import ExtractionResult, extract_document
from experimental.analyst.formats import DocumentFormat, sniff_document_format
from experimental.analyst.models import FileTerminal
from experimental.analyst.ooxml_contract import (
    DEFUSEDXML_VERSION,
    FRAME_MAGIC,
    MAX_EXPANDED_BYTES,
    MAX_MEMBERS,
    UNIT_SEPARATOR,
)
from experimental.analyst.ooxml_frame import decode_ooxml_frame
from experimental.analyst.sandbox import _inventory_for_fd

CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}
MAIN_PARTS = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
OFFICE_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _relationships(rows: list[tuple[str, str, str, str | None]]) -> str:
    rendered = []
    for rel_id, kind, target, mode in rows:
        mode_text = f' TargetMode="{mode}"' if mode else ""
        rendered.append(
            f'<Relationship Id="{rel_id}" Type="{kind}" '
            f'Target="{target}"{mode_text}/>'
        )
    return f'<Relationships xmlns="{PACKAGE_RELS}">{"".join(rendered)}</Relationships>'


def _package(
    path: Path,
    format_name: str,
    main_xml: str,
    *,
    parts: dict[str, str] | None = None,
    main_content_type: str | None = None,
    root_relation_type: str | None = None,
) -> None:
    main_part = MAIN_PARTS[format_name]
    content_type = main_content_type or CONTENT_TYPES[format_name]
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/{main_part}" ContentType="{content_type}"/>'
        "</Types>"
    )
    root_rels = _relationships([(
        "rId1",
        root_relation_type or f"{OFFICE_RELS}/officeDocument",
        main_part,
        None,
    )])
    entries = {
        "[Content_Types].xml": content_types,
        "_rels/.rels": root_rels,
        main_part: main_xml,
        **(parts or {}),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body.encode("utf-8"))


def _docx(path: Path, *, main_xml: str | None = None) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    main = main_xml or f'''<w:document xmlns:w="{word}" xmlns:a="{drawing}">
<w:body><w:p><w:r><w:t>Body α</w:t></w:r><w:tab/><w:r><w:t>line</w:t></w:r></w:p>
<w:p><w:del><w:r><w:delText>old</w:delText></w:r></w:del></w:p></w:body></w:document>'''
    rels = _relationships([(
        "rIdHeader", f"{OFFICE_RELS}/header", "header1.xml", None
    )])
    header = f'<w:hdr xmlns:w="{word}"><w:p><w:r><w:t>Header</w:t></w:r></w:p></w:hdr>'
    _package(path, "docx", main, parts={
        "word/_rels/document.xml.rels": rels,
        "word/header1.xml": header,
    })


def _xlsx(path: Path) -> None:
    sheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    workbook = f'''<workbook xmlns="{sheet}" xmlns:r="{rel}"><sheets>
<sheet name="Public Sheet" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels = _relationships([
        ("rId1", f"{OFFICE_RELS}/worksheet", "worksheets/sheet1.xml", None),
        ("rId2", f"{OFFICE_RELS}/sharedStrings", "sharedStrings.xml", None),
    ])
    shared = f'<sst xmlns="{sheet}"><si><t>Shared text</t></si></sst>'
    worksheet = f'''<worksheet xmlns="{sheet}"><sheetData><row r="1">
<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Inline</t></is></c>
<c r="C1"><v>42</v></c><c r="D1"><f>SUM(C1,1)</f><v>43</v></c>
</row></sheetData></worksheet>'''
    _package(path, "xlsx", workbook, parts={
        "xl/_rels/workbook.xml.rels": rels,
        "xl/sharedStrings.xml": shared,
        "xl/worksheets/sheet1.xml": worksheet,
    })


def _pptx(path: Path) -> None:
    presentation = "http://schemas.openxmlformats.org/presentationml/2006/main"
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    main = f'''<p:presentation xmlns:p="{presentation}" xmlns:r="{rel}">
<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/>
</p:sldIdLst></p:presentation>'''
    main_rels = _relationships([
        ("rId1", f"{OFFICE_RELS}/slide", "slides/slide1.xml", None),
        ("rId2", f"{OFFICE_RELS}/slide", "slides/slide2.xml", None),
    ])
    slide1 = f'<p:sld xmlns:p="{presentation}" xmlns:a="{drawing}"><a:p><a:t>ONE</a:t></a:p></p:sld>'
    slide2 = f'<p:sld xmlns:p="{presentation}" xmlns:a="{drawing}"><a:p><a:t>TWO</a:t></a:p></p:sld>'
    slide2_rels = _relationships([(
        "rIdNotes", f"{OFFICE_RELS}/notesSlide",
        "../notesSlides/notesSlide2.xml", None,
    )])
    notes = f'<p:notes xmlns:p="{presentation}" xmlns:a="{drawing}"><a:p><a:t>NOTE</a:t></a:p></p:notes>'
    _package(path, "pptx", main, parts={
        "ppt/_rels/presentation.xml.rels": main_rels,
        "ppt/slides/slide1.xml": slide1,
        "ppt/slides/slide2.xml": slide2,
        "ppt/slides/_rels/slide2.xml.rels": slide2_rels,
        "ppt/notesSlides/notesSlide2.xml": notes,
    })


def _extract_path(path: Path) -> ExtractionResult:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)


def _frame(header: dict, body: bytes = b"") -> bytes:
    return FRAME_MAGIC + json.dumps(
        header, sort_keys=True, separators=(",", ":")
    ).encode("ascii") + b"\n" + body


def _header(**changes) -> dict:
    header = {
        "defusedxml_version": DEFUSEDXML_VERSION,
        "detail": None,
        "expanded_bytes": 100,
        "format": "docx",
        "logical_unit_count": 2,
        "member_count": 3,
        "primary_unit_count": 2,
        "status": "success",
        "text_bytes": 5,
        "text_chars": 5,
        "units": [
            {"kind": "paragraph", "label": "main#p1", "text_chars": 2},
            {
                "kind": "paragraph", "label": "word/header1.xml#p1",
                "text_chars": 2,
            },
        ],
    }
    header.update(changes)
    return header


def test_zip_magic_routes_only_to_an_ooxml_candidate() -> None:
    for signature in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        assert sniff_document_format(signature) is DocumentFormat.OOXML_CONTAINER
    assert sniff_document_format(b"%PDF-1.7") is DocumentFormat.PDF


@pytest.mark.parametrize("builder", [_docx, _xlsx, _pptx])
def test_live_ooxml_formats_preserve_semantic_provenance(
    tmp_path: Path, builder,
) -> None:
    path = tmp_path / "public-container.bin"
    builder(path)
    result = _extract_path(path)
    assert result.ok, (result.reason, result.detail)
    assert result.parser_version == DEFUSEDXML_VERSION
    assert result.member_count >= 3
    assert result.expanded_bytes > 0
    assert result.logical_unit_count >= 1
    assert len(result.ooxml_units) >= 1
    assert result.text is not None
    assert tuple(len(value) for value in result.text.split(UNIT_SEPARATOR)) == \
        tuple(unit.char_count for unit in result.ooxml_units)


def test_live_docx_includes_referenced_stories_and_deleted_text(tmp_path: Path) -> None:
    path = tmp_path / "public.docx"
    _docx(path)
    result = _extract_path(path)
    assert result.format_name == "docx"
    assert result.text == "Body α\tline\f[deleted:old]\fHeader"
    assert [unit.label for unit in result.ooxml_units] == [
        "main#p1", "main#p2", "word/header1.xml#p1",
    ]


def test_live_xlsx_preserves_cells_and_never_evaluates_formulas(tmp_path: Path) -> None:
    path = tmp_path / "public.xlsx"
    _xlsx(path)
    result = _extract_path(path)
    assert result.format_name == "xlsx"
    assert result.text == (
        "Shared text\fInline\f42\fformula:SUM(C1,1)\tcached:43"
    )
    assert [unit.label for unit in result.ooxml_units] == [
        "sheet-1!A1", "sheet-1!B1", "sheet-1!C1", "sheet-1!D1",
    ]


def test_live_pptx_uses_presentation_order_and_linked_notes(tmp_path: Path) -> None:
    path = tmp_path / "public.pptx"
    _pptx(path)
    result = _extract_path(path)
    assert result.format_name == "pptx"
    assert result.text == "TWO\fNOTE\fONE"
    assert [(unit.kind, unit.label) for unit in result.ooxml_units] == [
        ("slide", "slide-1"),
        ("notes", "slide-1-notes"),
        ("slide", "slide-2"),
    ]


def test_blank_valid_ooxml_is_success_without_inventing_content(tmp_path: Path) -> None:
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    path = tmp_path / "blank.docx"
    _docx(path, main_xml=f'<w:document xmlns:w="{word}"><w:body/></w:document>')
    result = _extract_path(path)
    assert result.ok
    assert result.text == "Header"
    assert result.logical_unit_count == 1
    assert result.primary_unit_count == 2

    no_header = tmp_path / "truly-blank.docx"
    _package(
        no_header,
        "docx",
        f'<w:document xmlns:w="{word}"><w:body/></w:document>',
    )
    result = _extract_path(no_header)
    assert result.ok and result.text == ""
    assert result.logical_unit_count == 0 and result.ooxml_units == ()
    assert result.primary_unit_count == 1


def test_generic_zip_macro_and_strict_ooxml_are_explicitly_unsupported(
    tmp_path: Path,
) -> None:
    generic = tmp_path / "generic.zip"
    with zipfile.ZipFile(generic, "w") as archive:
        archive.writestr("readme.txt", "public")
    result = _extract_path(generic)
    assert (result.reason, result.detail) == (
        FileTerminal.UNSUPPORTED_FORMAT.value, "not_ooxml"
    )

    macro = tmp_path / "macro.zip"
    _package(
        macro, "docx", "<x/>",
        main_content_type="application/vnd.ms-word.document.macroEnabled.main+xml",
    )
    assert _extract_path(macro).detail == "macro_enabled"

    strict = tmp_path / "strict.zip"
    _package(
        strict, "docx", "<x/>",
        root_relation_type=(
            "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
        ),
    )
    assert _extract_path(strict).detail == "strict_ooxml"


@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE w:document [<!ENTITY x SYSTEM "file:///etc/passwd">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&x;</w:t></w:r></w:p></w:body></w:document>',
        '<!DOCTYPE w:document><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
    ],
)
def test_live_dtd_and_xxe_reach_defused_parser_and_fail_closed(
    tmp_path: Path, payload: str,
) -> None:
    path = tmp_path / "hostile.docx"
    _package(path, "docx", payload)
    result = _extract_path(path)
    assert result.reason == FileTerminal.PARSE_ERROR.value
    assert result.detail == "xml_parse"
    assert result.text is None


def test_strict_frame_validates_provenance_types_and_delimiters() -> None:
    valid = decode_ooxml_frame(
        _frame(_header(), b"aa\fbb"), max_text_bytes=100, max_text_chars=100
    )
    assert valid.reason == "success" and len(valid.units) == 2
    invalid = [
        (_header(member_count=True), b"aa\fbb"),
        (_header(logical_unit_count=1), b"aa\fbb"),
        (_header(expanded_bytes=MAX_EXPANDED_BYTES + 1), b"aa\fbb"),
        (_header(units=[{
            "kind": "paragraph", "label": "main#p1", "text_chars": 5,
        }]),
         b"aa\fbb"),
        (_header(units=[
            {"kind": "sheet", "label": "main#p1", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p2", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(units=[
            {"kind": "paragraph", "label": "main#p1", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p1", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(units=[
            {"kind": "paragraph", "label": "word/./header.xml#p1", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p2", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(units=[
            {"kind": "paragraph", "label": "word/%2e%2e/x.xml#p1", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p2", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(units=[
            {"kind": "paragraph", "label": "word/header\\evil.xml#p1", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p2", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(units=[
            {"kind": "paragraph", "label": "main#p100001", "text_chars": 2},
            {"kind": "paragraph", "label": "main#p2", "text_chars": 2},
        ]), b"aa\fbb"),
        (_header(
            format="xlsx", logical_unit_count=1, primary_unit_count=0,
            text_bytes=2, text_chars=2,
            units=[{"kind": "cell", "label": "sheet-1!A1", "text_chars": 2}],
        ), b"aa"),
        (_header(
            format="pptx", logical_unit_count=1, primary_unit_count=1,
            text_bytes=2, text_chars=2,
            units=[{
                "kind": "notes", "label": "slide-2-notes", "text_chars": 2,
            }],
        ), b"aa"),
        (_header(defusedxml_version="0.7.0"), b"aa\fbb"),
        (_header(extra="nope"), b"aa\fbb"),
        (_header(), b"aa\x00bb"),
    ]
    for header, body in invalid:
        assert decode_ooxml_frame(
            _frame(header, body), max_text_bytes=100, max_text_chars=100
        ).reason == FileTerminal.PARSE_ERROR.value
    duplicate = FRAME_MAGIC + b'{"detail":null,"detail":null}\n'
    assert decode_ooxml_frame(
        duplicate, max_text_bytes=100, max_text_chars=100
    ).reason == FileTerminal.PARSE_ERROR.value


def test_seeded_hostile_ooxml_frames_never_escape_decoder() -> None:
    rng = random.Random(20260815)
    for _ in range(1000):
        payload = rng.randbytes(rng.randrange(0, 1025))
        assert decode_ooxml_frame(
            payload, max_text_bytes=100, max_text_chars=100
        ).reason == FileTerminal.PARSE_ERROR.value


def test_missing_and_wrong_dependency_stop_before_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "candidate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("public.txt", "public")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    monkeypatch.setattr(
        extract.metadata,
        "distribution",
        lambda _name: (_ for _ in ()).throw(extract.metadata.PackageNotFoundError()),
    )
    monkeypatch.setattr(
        extract, "run_sandboxed",
        lambda **_kwargs: pytest.fail("missing dependency reached sandbox"),
    )
    try:
        result = extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)
    assert (result.reason, result.detail) == (
        FileTerminal.SANDBOX_UNAVAILABLE.value, "dependency_missing"
    )

    monkeypatch.setattr(
        extract.metadata,
        "distribution",
        lambda _name: SimpleNamespace(version="0.7.0"),
    )
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        result = extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)
    assert result.detail == "dependency_version"


def test_ooxml_runtime_bind_is_narrow_and_tracks_every_exact_file() -> None:
    package = extract._defusedxml_package_root()
    bindings = extract.ooxml_runtime_binds()
    sources = {binding.source for binding in bindings}
    destinations = {binding.destination for binding in bindings}
    assert package in sources
    assert extract.OOXML_CHILD_PATH in sources
    assert extract.OOXML_CONTRACT_PATH in sources
    assert extract.OOXML_SITE_DESTINATION in destinations
    assert extract.OOXML_CHILD_DESTINATION in destinations
    assert extract.OOXML_CONTRACT_DESTINATION in destinations
    assert Path(sys.prefix).resolve() not in sources
    assert Path("/usr") not in sources
    assert Path.home() not in sources
    assert extract.CHILD_PATH not in sources
    assert extract.PDF_CHILD_PATH not in sources


def test_defusedxml_import_exists_only_in_ooxml_child() -> None:
    package_dir = Path(extract.__file__).parent
    offenders: list[str] = []
    for path in package_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] == "defusedxml" for name in names):
                if path.name != "ooxml_child.py":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders

    source = (
        "import sys; import experimental.analyst; "
        "import experimental.analyst.extract; "
        "assert 'defusedxml' not in sys.modules"
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


def test_ooxml_contract_accepts_exact_member_cap_and_rejects_cap_plus_one() -> None:
    class FakeArchive:
        def __init__(self, count: int) -> None:
            self._infos = [
                SimpleNamespace(
                    filename=f"part-{index}.xml", flag_bits=0,
                    compress_type=zipfile.ZIP_STORED, file_size=0,
                    compress_size=0, external_attr=0, is_dir=lambda: False,
                )
                for index in range(count)
            ]

        def infolist(self):
            return self._infos

    members, _expanded = ooxml_child._inspect_archive(FakeArchive(MAX_MEMBERS))
    assert len(members) == MAX_MEMBERS
    with pytest.raises(ooxml_child.OutputLimit, match="member_limit"):
        ooxml_child._inspect_archive(FakeArchive(MAX_MEMBERS + 1))


def test_defusedxml_pin_and_psf_notice_cannot_silently_drift() -> None:
    root = Path(__file__).resolve().parents[2]
    requirements = (
        root / "experimental" / "analyst" / "requirements-analyst.txt"
    ).read_text(encoding="utf-8")
    assert "defusedxml==0.7.1" in requirements
    assert "a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61" \
        in requirements
    notice = (root / "licenses" / "defusedxml-NOTICE.md").read_text(
        encoding="utf-8"
    )
    assert "Copyright (c) 2013–2017 by Christian Heimes" in notice
    assert "Python Software Foundation License Version 2" in notice
    license_body = (root / "licenses" / "defusedxml-PSF-2.0.txt").read_bytes()
    assert b"PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2" in license_body
    assert hashlib.sha256(license_body).hexdigest() == \
        "b80ce9da8c42a1f91079627fbbe2bf27210ae108a0ffe5f077d5b08e076c24c8"
