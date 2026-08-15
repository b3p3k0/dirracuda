"""C5 optional, sandboxed PDF extraction contracts."""

from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from experimental.analyst import extract, pdf_child
from experimental.analyst.extract import ExtractionResult, extract_document
from experimental.analyst.formats import (
    DocumentFormat,
    sniff_document_format,
    sniff_text_format,
)
from experimental.analyst.models import FileTerminal
from experimental.analyst.sandbox import _inventory_for_fd
from scripts.analyst_benchmark.sandbox_smoke import minimal_pdf


def _source(path: Path, body: bytes):
    path.write_bytes(body)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    return fd, _inventory_for_fd(fd)


def _require_pymupdf() -> None:
    if importlib.util.find_spec("pymupdf") is None:
        pytest.skip("optional PyMuPDF dependency is not installed")


def _make_pdf(path: Path, mode: str) -> None:
    _require_pymupdf()
    source = r'''import os, sys
os.environ["PYMUPDF_MESSAGE"] = "fd:2"
import pymupdf
path, mode = sys.argv[1:]
doc = pymupdf.open()
if mode == "pages":
    first = doc.new_page(width=300, height=200)
    first.insert_text((20, 150), "BOTTOM")
    first.insert_text((20, 50), "TOP")
    doc.new_page(width=300, height=200)
    third = doc.new_page(width=300, height=200)
    third.insert_text((20, 50), "THIRD PAGE")
elif mode == "blank":
    page = doc.new_page(width=300, height=200)
    page.draw_rect((20, 20, 80, 80))
elif mode == "encrypted":
    page = doc.new_page(width=300, height=200)
    page.insert_text((20, 50), "PUBLIC PASSWORD TEST")
else:
    raise SystemExit(64)
kwargs = {}
if mode == "encrypted":
    kwargs = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                  owner_pw="owner-public", user_pw="user-public")
doc.save(path, **kwargs)
doc.close()
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source, str(path), mode],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
        shell=False,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")


def _extract_path(path: Path) -> ExtractionResult:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return extract_document(source_fd=fd, expected=_inventory_for_fd(fd))
    finally:
        os.close(fd)


def _pdf_frame(header: dict, body: bytes = b"") -> bytes:
    return extract.PDF_FRAME_MAGIC + json.dumps(
        header, sort_keys=True, separators=(",", ":")
    ).encode("ascii") + b"\n" + body


def _header(**changes) -> dict:
    header = {
        "detail": None,
        "format": "pdf",
        "mupdf_version": extract.MUPDF_VERSION,
        "page_char_counts": [2, 2],
        "page_count": 2,
        "pymupdf_version": extract.PYMUPDF_VERSION,
        "status": "success",
        "text_bytes": 5,
        "text_chars": 5,
        "text_page_count": 2,
    }
    header.update(changes)
    return header


def test_document_sniffer_routes_pdf_without_weakening_text_sniffer() -> None:
    assert sniff_document_format(b"%PDF-1.7\n") is DocumentFormat.PDF
    assert sniff_text_format(b"%PDF-1.7\n") is None
    assert sniff_document_format(b"PK\x03\x04not-a-pdf") is None


def test_child_rejects_a_non_pdf_even_if_upstream_opens_it() -> None:
    class NotPdf:
        is_pdf = False

        def close(self) -> None:
            pass

    fake = SimpleNamespace(open=lambda **_kwargs: NotPdf())
    with pytest.raises(pdf_child.ParseFailure, match="format_mismatch"):
        pdf_child._extract(fake, 10, 100, 100)


def test_child_discards_whitespace_only_pages_as_no_text() -> None:
    class WhitespacePage:
        def get_text(self, *_args, **_kwargs) -> str:
            return " \n"

    class WhitespacePdf:
        is_pdf = True
        needs_pass = 0
        page_count = 2

        def load_page(self, _number: int) -> WhitespacePage:
            return WhitespacePage()

        def close(self) -> None:
            pass

    fake = SimpleNamespace(open=lambda **_kwargs: WhitespacePdf())
    output = pdf_child._extract(fake, 10, 100, 100)
    assert output == pdf_child.PdfOutput(
        "no_text_layer", "no_text_layer", page_char_counts=(0, 0)
    )


def test_live_pdf_preserves_sorted_page_text_and_blank_page_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public-pages.bin"
    _make_pdf(path, "pages")
    result = _extract_path(path)
    assert result.ok
    assert result.format_name == "pdf"
    assert result.parser_version == "1.28.0"
    assert result.embedded_version == "1.28.0"
    assert result.text_page_count == 2
    assert len(result.page_char_counts) == 3
    assert result.page_char_counts[1] == 0
    assert result.text is not None
    pages = result.text.split(extract.PDF_PAGE_SEPARATOR)
    assert len(pages) == 3
    assert pages[0].index("TOP") < pages[0].index("BOTTOM")
    assert pages[1] == ""
    assert "THIRD PAGE" in pages[2]
    assert tuple(map(len, pages)) == result.page_char_counts


def test_live_blank_pdf_is_explicitly_no_text_layer(tmp_path: Path) -> None:
    path = tmp_path / "public-blank.pdf"
    _make_pdf(path, "blank")
    result = _extract_path(path)
    assert result.reason == FileTerminal.NO_TEXT_LAYER.value
    assert result.text is None
    assert result.page_char_counts == (0,)
    assert result.parser_version == "1.28.0"
    assert result.embedded_version == "1.28.0"


def test_live_encrypted_pdf_does_not_attempt_a_password(tmp_path: Path) -> None:
    path = tmp_path / "public-encrypted.pdf"
    _make_pdf(path, "encrypted")
    result = _extract_path(path)
    assert result.reason == FileTerminal.ENCRYPTED.value
    assert result.detail == "password_required"
    assert result.text is None


def test_live_malformed_pdf_diagnostics_cannot_corrupt_frame(tmp_path: Path) -> None:
    path = tmp_path / "public-malformed.pdf"
    path.write_bytes(b"%PDF-1.7\nnot a document\n%%EOF\n")
    result = _extract_path(path)
    assert result.reason == FileTerminal.PARSE_ERROR.value
    assert result.text is None


def test_live_repair_diagnostics_are_redirected_away_from_success_frame(
    tmp_path: Path,
) -> None:
    path = tmp_path / "public-repairable.pdf"
    body = re.sub(
        rb"startxref\n[0-9]+", b"startxref\n0", minimal_pdf("REPAIR TEXT")
    )
    path.write_bytes(body)
    result = _extract_path(path)
    assert result.ok
    assert result.text == "REPAIR TEXT"


def test_live_page_and_text_limits_discard_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "public-limits.pdf"
    _make_pdf(path, "pages")
    monkeypatch.setattr(extract, "MAX_PDF_PAGES", 2)
    result = _extract_path(path)
    assert result.reason == FileTerminal.PARSER_OUTPUT_LIMIT.value
    assert result.detail == "page_limit"
    assert result.text is None

    monkeypatch.setattr(extract, "MAX_PDF_PAGES", 10_000)
    monkeypatch.setattr(extract, "MAX_TEXT_BYTES", 4)
    monkeypatch.setattr(extract, "MAX_TEXT_CHARS", 4)
    result = _extract_path(path)
    assert result.reason == FileTerminal.PARSER_OUTPUT_LIMIT.value
    assert result.detail == "text_limit"
    assert result.text is None


def test_missing_optional_dependency_stops_before_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "public.pdf"
    path.write_bytes(b"%PDF-1.7\n")
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
    assert result.reason == FileTerminal.SANDBOX_UNAVAILABLE.value
    assert result.detail == "dependency_missing"


def test_host_package_version_mismatch_stops_before_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "public-version.pdf"
    fd, expected = _source(path, b"%PDF-1.7\n")
    monkeypatch.setattr(
        extract.metadata, "distribution",
        lambda _name: SimpleNamespace(version="1.27.0"),
    )
    monkeypatch.setattr(
        extract, "run_sandboxed",
        lambda **_kwargs: pytest.fail("version mismatch reached sandbox"),
    )
    try:
        result = extract_document(source_fd=fd, expected=expected)
    finally:
        os.close(fd)
    assert result.reason == FileTerminal.SANDBOX_UNAVAILABLE.value
    assert result.detail == "dependency_version"


def test_pdf_frame_strictly_validates_versions_pages_and_types() -> None:
    valid = _pdf_frame(_header(), b"aa\fbb")
    result = extract._decode_pdf_frame(valid)
    assert result.ok and result.page_char_counts == (2, 2)
    invalid = [
        (_header(page_count=True), b"aa\fbb"),
        (_header(page_char_counts=[2, True]), b"aa\fbb"),
        (_header(page_char_counts=[2], page_count=1), b"aa\fbb"),
        (_header(text_chars=4), b"aa\fbb"),
        (_header(text_page_count=1), b"aa\fbb"),
        (_header(pymupdf_version="1.27.0"), b"aa\fbb"),
        (_header(mupdf_version="1.29.0"), b"aa\fbb"),
        (_header(extra="nope"), b"aa\fbb"),
        (_header(status="parse_error", detail="pdf_parse"), b"aa\fbb"),
        (_header(page_char_counts=[3, 2], text_bytes=6, text_chars=6),
         b"a\x00b\fbb"),
    ]
    for header, body in invalid:
        assert extract._decode_pdf_frame(_pdf_frame(header, body)).reason == \
            FileTerminal.PARSE_ERROR.value
    bad_utf8 = _header(page_char_counts=[1], page_count=1, text_bytes=1,
                       text_chars=1, text_page_count=1)
    assert extract._decode_pdf_frame(_pdf_frame(bad_utf8, b"\xff")).reason == \
        FileTerminal.PARSE_ERROR.value
    duplicate = (
        extract.PDF_FRAME_MAGIC
        + b'{"detail":null,"detail":null}\n'
    )
    assert extract._decode_pdf_frame(duplicate).reason == FileTerminal.PARSE_ERROR.value


def test_pdf_failure_frames_are_closed_and_version_mismatch_is_unavailable() -> None:
    no_text = _header(
        status="no_text_layer", detail="no_text_layer",
        page_char_counts=[0, 0], text_bytes=0, text_chars=0, text_page_count=0,
    )
    assert extract._decode_pdf_frame(_pdf_frame(no_text)).reason == \
        FileTerminal.NO_TEXT_LAYER.value
    mismatch = _header(
        status="dependency_unavailable", detail="dependency_version",
        pymupdf_version="1.28.0", mupdf_version="1.29.0",
        page_char_counts=[], page_count=0, text_bytes=0, text_chars=0,
        text_page_count=0,
    )
    result = extract._decode_pdf_frame(_pdf_frame(mismatch))
    assert result.reason == FileTerminal.SANDBOX_UNAVAILABLE.value
    assert result.embedded_version == "1.29.0"


@pytest.mark.parametrize(
    ("python_version", "mupdf_version", "detail"),
    [
        ("1.27.0", "1.28.0", "dependency_version"),
        ("1.28.0", "1.29.0", "dependency_version"),
        (None, None, "dependency_missing"),
    ],
)
def test_child_fails_closed_on_each_dependency_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    python_version: str | None,
    mupdf_version: str | None,
    detail: str,
) -> None:
    original_import = builtins.__import__

    def controlled_import(name, *args, **kwargs):
        if name != "pymupdf":
            return original_import(name, *args, **kwargs)
        if python_version is None:
            raise ImportError("synthetic missing dependency")
        return SimpleNamespace(
            pymupdf_version=python_version, mupdf_version=mupdf_version
        )

    frames: list[tuple[str, dict]] = []
    monkeypatch.setenv("PYMUPDF_MESSAGE", "synthetic-before-import")
    monkeypatch.setattr(builtins, "__import__", controlled_import)
    monkeypatch.setattr(
        pdf_child, "_write_frame",
        lambda status, **values: frames.append((status, values)),
    )
    assert pdf_child._load_pymupdf() is None
    assert frames == [("dependency_unavailable", {
        "detail": detail,
        "pymupdf_version": python_version,
        "mupdf_version": mupdf_version,
    })]
    assert os.environ["PYMUPDF_MESSAGE"] == "fd:2"


def test_seeded_hostile_pdf_frames_never_escape_the_decoder() -> None:
    rng = random.Random(20260814)
    for _ in range(1000):
        payload = rng.randbytes(rng.randrange(0, 1025))
        result = extract._decode_pdf_frame(payload)
        assert result.reason == FileTerminal.PARSE_ERROR.value


def test_pdf_runtime_bind_is_narrow_and_cache_tracks_package_files() -> None:
    _require_pymupdf()
    package = extract._pdf_package_root()
    bindings = extract.pdf_runtime_binds()
    sources = {binding.source for binding in bindings}
    destinations = {binding.destination for binding in bindings}
    venv_root = Path(sys.prefix).resolve()
    assert package in sources
    assert extract.PDF_CHILD_PATH in sources
    assert extract.PDF_SITE_DESTINATION in destinations
    assert extract.PDF_CHILD_DESTINATION in destinations
    assert venv_root not in sources
    assert Path("/usr") not in sources
    assert Path.home() not in sources
    assert extract.CHILD_PATH not in sources
    identity = extract._package_tree_identity(package)
    assert any(item[0] == "_mupdf.so" for item in identity)


def test_pymupdf_import_exists_only_in_the_sandbox_child() -> None:
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
            if any(name.split(".")[0] in {"pymupdf", "fitz"} for name in names):
                if path.name != "pdf_child.py":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders

    source = (
        "import sys; import experimental.analyst; "
        "import experimental.analyst.extract; "
        "assert 'pymupdf' not in sys.modules and 'fitz' not in sys.modules"
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


def test_source_pin_and_agpl_notices_cannot_silently_drift() -> None:
    root = Path(__file__).resolve().parents[2]
    requirements = (
        root / "experimental" / "analyst" / "requirements-analyst.txt"
    ).read_text(encoding="utf-8")
    assert "--no-binary PyMuPDF" in requirements
    assert "PyMuPDF==1.28.0" in requirements
    assert "e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d" \
        in requirements

    notice = (root / "licenses" / "PyMuPDF-MuPDF-NOTICE.md").read_text(
        encoding="utf-8"
    )
    assert "PyMuPDF 1.28.0" in notice
    assert "MuPDF 1.28.0" in notice
    assert "GNU Affero General Public License, version 3" in notice
    agpl = (root / "licenses" / "AGPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in agpl
    assert "Version 3, 19 November 2007" in agpl
