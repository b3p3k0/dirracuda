"""Standalone OOXML parser executed only inside the C3 sandbox."""

from __future__ import annotations

import io
import json
import os
import posixpath
import re
import stat
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass

INPUT_PATH = "/input/document"
RUNTIME_PATH = "/runtime"
SITE_PACKAGES = "/runtime/site-packages"

if __package__:
    from . import ooxml_contract as _contract
else:
    if RUNTIME_PATH not in sys.path:
        sys.path.insert(0, RUNTIME_PATH)
    import ooxml_contract as _contract  # type: ignore[import-not-found]

DEFUSEDXML_VERSION = _contract.DEFUSEDXML_VERSION
FRAME_MAGIC = _contract.FRAME_MAGIC
MAX_ATTRIBUTES_PER_ELEMENT = _contract.MAX_ATTRIBUTES_PER_ELEMENT
MAX_ATTRIBUTE_CHARS = _contract.MAX_ATTRIBUTE_CHARS
MAX_CELLS = _contract.MAX_CELLS
MAX_COMPRESSION_RATIO = _contract.MAX_COMPRESSION_RATIO
MAX_EXPANDED_BYTES = _contract.MAX_EXPANDED_BYTES
MAX_HEADER_BYTES = _contract.MAX_HEADER_BYTES
MAX_LOGICAL_UNITS = _contract.MAX_LOGICAL_UNITS
MAX_MEMBER_BYTES = _contract.MAX_MEMBER_BYTES
MAX_MEMBER_NAME_CHARS = _contract.MAX_MEMBER_NAME_CHARS
MAX_MEMBERS = _contract.MAX_MEMBERS
MAX_SHEETS = _contract.MAX_SHEETS
MAX_SLIDES = _contract.MAX_SLIDES
MAX_UNIT_LABEL_CHARS = _contract.MAX_UNIT_LABEL_CHARS
MAX_XML_DEPTH = _contract.MAX_XML_DEPTH
MAX_XML_ELEMENTS_PACKAGE = _contract.MAX_XML_ELEMENTS_PACKAGE
MAX_XML_ELEMENTS_PER_PART = _contract.MAX_XML_ELEMENTS_PER_PART
MAX_XML_MEMBER_BYTES = _contract.MAX_XML_MEMBER_BYTES
MAX_XML_PACKAGE_BYTES = _contract.MAX_XML_PACKAGE_BYTES
UNIT_SEPARATOR = _contract.UNIT_SEPARATOR

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_BASE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
STRICT_REL_BASE = "http://purl.oclc.org/ooxml/officeDocument/relationships"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PRESENTATION_NS = (
    "http://schemas.openxmlformats.org/presentationml/2006/main"
)
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REL_ATTR = f"{{{OFFICE_REL_BASE}}}id"

MAIN_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
        "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":
        "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml":
        "pptx",
}
MACRO_CONTENT_TYPES = {
    "application/vnd.ms-word.document.macroEnabled.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
}
FORMAT_PREFIXES = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
ZIP_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
CELL_REFERENCE = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})\Z")
ENCODED_PATH_ESCAPE = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)


class ParseFailure(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class OutputLimit(ParseFailure):
    pass


class UnsupportedFormat(ParseFailure):
    pass


@dataclass(frozen=True, slots=True)
class Relationship:
    kind: str
    target: str
    external: bool


@dataclass(frozen=True, slots=True)
class Unit:
    kind: str
    label: str
    text: str


@dataclass(slots=True)
class XmlBudget:
    bytes_read: int = 0
    elements: int = 0


class Output:
    def __init__(self, max_bytes: int, max_chars: int) -> None:
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.units: list[Unit] = []
        self.logical_unit_count = 0
        self.primary_unit_count = 0
        self.byte_count = 0
        self.char_count = 0

    def add(self, kind: str, label: str, text: str) -> None:
        label = _safe_label(label)
        text = _safe_text(text)
        if not text.strip():
            return
        self.logical_unit_count += 1
        if self.logical_unit_count > MAX_LOGICAL_UNITS:
            raise OutputLimit("semantic_unit_limit")
        separator = 1 if self.units else 0
        encoded = text.encode("utf-8", errors="strict")
        if (
            self.byte_count + separator + len(encoded) > self.max_bytes
            or self.char_count + separator + len(text) > self.max_chars
        ):
            raise OutputLimit("text_limit")
        self.units.append(Unit(kind, label, text))
        self.byte_count += separator + len(encoded)
        self.char_count += separator + len(text)

    def finish(self) -> str:
        return UNIT_SEPARATOR.join(unit.text for unit in self.units)


def _positive(raw: str) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise ValueError
    value = int(raw)
    if value <= 0:
        raise ValueError
    return value


def _write_frame(
    status: str,
    *,
    format_name: str = "ooxml",
    detail: str | None = None,
    output: Output | None = None,
    member_count: int = 0,
    expanded_bytes: int = 0,
    defusedxml_version: str | None = DEFUSEDXML_VERSION,
) -> None:
    text = output.finish() if output is not None else ""
    body = text.encode("utf-8", errors="strict")
    units = [
        {"kind": unit.kind, "label": unit.label, "text_chars": len(unit.text)}
        for unit in (output.units if output is not None else ())
    ]
    header = {
        "defusedxml_version": defusedxml_version,
        "detail": detail,
        "expanded_bytes": expanded_bytes,
        "format": format_name,
        "logical_unit_count": (
            output.logical_unit_count if output is not None else 0
        ),
        "member_count": member_count,
        "primary_unit_count": output.primary_unit_count if output is not None else 0,
        "status": status,
        "text_bytes": len(body),
        "text_chars": len(text),
        "units": units,
    }
    encoded = json.dumps(
        header, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    if len(encoded) > MAX_HEADER_BYTES:
        if status != "success":
            raise RuntimeError("failure frame exceeded fixed header limit")
        _write_frame(
            "parser_output_limit",
            format_name=format_name,
            detail="semantic_unit_limit",
        )
        return
    sys.stdout.buffer.write(FRAME_MAGIC + encoded + b"\n" + body)
    sys.stdout.buffer.flush()


def _load_xml_parser():
    if SITE_PACKAGES not in sys.path:
        sys.path.insert(0, SITE_PACKAGES)
    try:
        import defusedxml  # type: ignore[import-not-found]
        from defusedxml import ElementTree  # type: ignore[import-not-found]
    except Exception:
        _write_frame(
            "dependency_unavailable",
            detail="dependency_missing",
            defusedxml_version=None,
        )
        return None
    observed = getattr(defusedxml, "__version__", None)
    if observed != DEFUSEDXML_VERSION:
        _write_frame(
            "dependency_unavailable",
            detail="dependency_version",
            defusedxml_version=observed if type(observed) is str else None,
        )
        return None
    return ElementTree


def _safe_text(value: object) -> str:
    if type(value) is not str:
        raise ParseFailure("text_type")
    if any(
        char == "\x00"
        or ord(char) < 32 and char not in "\t\n\r"
        or 127 <= ord(char) < 160
        for char in value
    ):
        raise ParseFailure("control_character")
    return value


def _safe_label(value: object) -> str:
    text = _safe_text(value)
    if (
        not 1 <= len(text) <= MAX_UNIT_LABEL_CHARS
        or not text.strip()
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in text)
    ):
        raise ParseFailure("text_type")
    return text


def _inspect_archive(archive: zipfile.ZipFile) -> tuple[dict[str, zipfile.ZipInfo], int]:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise OutputLimit("member_limit")
    if not infos:
        raise UnsupportedFormat("not_ooxml")
    members: dict[str, zipfile.ZipInfo] = {}
    normalized: set[str] = set()
    expanded = 0
    compressed = 0
    for info in infos:
        name = _validate_member_name(info)
        folded = name.casefold().rstrip("/")
        if name in members or folded in normalized:
            raise ParseFailure("archive_duplicate")
        members[name] = info
        normalized.add(folded)
        if info.flag_bits & 1:
            raise ParseFailure("archive_encrypted")
        if info.compress_type not in ZIP_METHODS:
            raise UnsupportedFormat("compression_method")
        if (
            type(info.file_size) is not int
            or type(info.compress_size) is not int
            or info.file_size < 0
            or info.compress_size < 0
        ):
            raise ParseFailure("archive_corrupt")
        if info.file_size > MAX_MEMBER_BYTES:
            raise OutputLimit("member_size")
        expanded += info.file_size
        compressed += info.compress_size
        if expanded > MAX_EXPANDED_BYTES:
            raise OutputLimit("expanded_limit")
        if (
            info.file_size > 0
            and (
                info.compress_size == 0
                or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
            )
        ):
            raise OutputLimit("member_ratio")
    if expanded > max(compressed, 1) * MAX_COMPRESSION_RATIO:
        raise OutputLimit("aggregate_ratio")
    return members, expanded


def _validate_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if (
        type(name) is not str
        or not 1 <= len(name) <= MAX_MEMBER_NAME_CHARS
        or name.startswith(("/", "\\"))
        or "//" in name
        or "\\" in name
        or ENCODED_PATH_ESCAPE.search(name)
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in name)
    ):
        raise ParseFailure("archive_path")
    raw_parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if (
        any(part in {"", ".", ".."} for part in raw_parts)
        or (raw_parts and ":" in raw_parts[0])
    ):
        raise ParseFailure("archive_path")
    is_dir = info.is_dir()
    if name.endswith("/") != is_dir:
        raise ParseFailure("archive_path")
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ParseFailure("archive_path")
    if is_dir and file_type == stat.S_IFREG:
        raise ParseFailure("archive_path")
    return name


def _read_tree(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    name: str,
    parser,
    budget: XmlBudget,
):
    info = members.get(name)
    if info is None or info.is_dir():
        raise ParseFailure("relationship")
    if info.file_size > MAX_XML_MEMBER_BYTES:
        raise OutputLimit("xml_size")
    if budget.bytes_read + info.file_size > MAX_XML_PACKAGE_BYTES:
        raise OutputLimit("xml_package_limit")
    try:
        with archive.open(info, "r") as source:
            chunks: list[bytes] = []
            observed = 0
            while True:
                chunk = source.read(64 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > MAX_XML_MEMBER_BYTES:
                    raise OutputLimit("xml_size")
                if budget.bytes_read + observed > MAX_XML_PACKAGE_BYTES:
                    raise OutputLimit("xml_package_limit")
                chunks.append(chunk)
    except (OutputLimit, ParseFailure):
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise ParseFailure("archive_corrupt") from exc
    if observed != info.file_size:
        raise ParseFailure("archive_corrupt")
    budget.bytes_read += observed
    data = b"".join(chunks)
    depth = 0
    elements = 0
    try:
        iterator = parser.iterparse(
            io.BytesIO(data),
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        for event, element in iterator:
            if event == "start":
                depth += 1
                elements += 1
                if depth > MAX_XML_DEPTH:
                    raise OutputLimit("xml_depth")
                if (
                    elements > MAX_XML_ELEMENTS_PER_PART
                    or budget.elements + elements > MAX_XML_ELEMENTS_PACKAGE
                ):
                    raise OutputLimit("xml_element_limit")
                if len(element.attrib) > MAX_ATTRIBUTES_PER_ELEMENT:
                    raise ParseFailure("attribute_limit")
                for key, value in element.attrib.items():
                    if (
                        type(key) is not str
                        or type(value) is not str
                        or len(value) > MAX_ATTRIBUTE_CHARS
                    ):
                        raise ParseFailure("attribute_limit")
            else:
                depth -= 1
                if depth < 0:
                    raise ParseFailure("xml_parse")
        root = iterator.root
    except (OutputLimit, ParseFailure):
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise ParseFailure("xml_parse") from exc
    if root is None or depth != 0:
        raise ParseFailure("xml_parse")
    budget.elements += elements
    return root


def _identify_package(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    parser,
    budget: XmlBudget,
) -> tuple[str, str]:
    if "[Content_Types].xml" not in members or "_rels/.rels" not in members:
        raise UnsupportedFormat("not_ooxml")
    root_rels = _relationships(
        archive, members, "", parser, budget, required=True
    )
    office = [
        value for value in root_rels.values()
        if value.kind in {
            f"{OFFICE_REL_BASE}/officeDocument",
            f"{STRICT_REL_BASE}/officeDocument",
        }
    ]
    if len(office) != 1 or office[0].external:
        raise ParseFailure("main_relationship")
    if office[0].kind.startswith(STRICT_REL_BASE):
        raise UnsupportedFormat("strict_ooxml")
    main_part = _resolve_target("", office[0].target)
    content_root = _read_tree(
        archive, members, "[Content_Types].xml", parser, budget
    )
    if content_root.tag != f"{{{CONTENT_TYPES_NS}}}Types":
        raise ParseFailure("content_types")
    overrides: dict[str, str] = {}
    recognized: list[tuple[str, str]] = []
    for child in content_root:
        if child.tag != f"{{{CONTENT_TYPES_NS}}}Override":
            continue
        raw_part = child.attrib.get("PartName")
        content_type = child.attrib.get("ContentType")
        if type(raw_part) is not str or type(content_type) is not str:
            raise ParseFailure("content_types")
        part = _content_part(raw_part)
        if part in overrides:
            raise ParseFailure("content_types")
        overrides[part] = content_type
        if content_type in MAIN_CONTENT_TYPES:
            recognized.append((part, MAIN_CONTENT_TYPES[content_type]))
    selected_type = overrides.get(main_part)
    if selected_type in MACRO_CONTENT_TYPES:
        raise UnsupportedFormat("macro_enabled")
    if selected_type not in MAIN_CONTENT_TYPES:
        raise UnsupportedFormat("not_ooxml")
    format_name = MAIN_CONTENT_TYPES[selected_type]
    if recognized != [(main_part, format_name)]:
        raise ParseFailure("content_types")
    if (
        main_part not in members
        or not main_part.startswith(FORMAT_PREFIXES[format_name])
        or not main_part.endswith(".xml")
    ):
        raise ParseFailure("main_relationship")
    return format_name, main_part


def _content_part(raw: str) -> str:
    if not raw.startswith("/") or raw.startswith("//"):
        raise ParseFailure("content_types")
    return _normalize_package_path(raw[1:], allow_parent=False)


def _relationships(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    source_part: str,
    parser,
    budget: XmlBudget,
    *,
    required: bool,
) -> dict[str, Relationship]:
    rel_name = _relationship_part(source_part)
    if rel_name not in members:
        if required:
            raise ParseFailure("relationship")
        return {}
    root = _read_tree(archive, members, rel_name, parser, budget)
    if root.tag != f"{{{PACKAGE_REL_NS}}}Relationships":
        raise ParseFailure("relationship")
    found: dict[str, Relationship] = {}
    for child in root:
        if child.tag != f"{{{PACKAGE_REL_NS}}}Relationship":
            raise ParseFailure("relationship")
        rel_id = child.attrib.get("Id")
        kind = child.attrib.get("Type")
        target = child.attrib.get("Target")
        mode = child.attrib.get("TargetMode")
        if (
            type(rel_id) is not str
            or not 1 <= len(rel_id) <= 256
            or type(kind) is not str
            or not 1 <= len(kind) <= 512
            or type(target) is not str
            or not 1 <= len(target) <= MAX_MEMBER_NAME_CHARS
            or mode not in {None, "Internal", "External"}
            or rel_id in found
        ):
            raise ParseFailure("relationship")
        found[rel_id] = Relationship(kind, target, mode == "External")
    return found


def _relationship_part(source_part: str) -> str:
    if not source_part:
        return "_rels/.rels"
    parent, name = posixpath.split(source_part)
    return f"{parent}/_rels/{name}.rels" if parent else f"_rels/{name}.rels"


def _resolve_target(source_part: str, target: str) -> str:
    if (
        "\\" in target
        or ENCODED_PATH_ESCAPE.search(target)
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in target)
    ):
        raise ParseFailure("relationship")
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ParseFailure("relationship")
    if parsed.path.startswith("//"):
        raise ParseFailure("relationship")
    if parsed.path.startswith("/"):
        combined = parsed.path[1:]
    else:
        parent = posixpath.dirname(source_part)
        combined = f"{parent}/{parsed.path}" if parent else parsed.path
    return _normalize_package_path(combined, allow_parent=True)


def _normalize_package_path(value: str, *, allow_parent: bool) -> str:
    if (
        not value
        or len(value) > MAX_MEMBER_NAME_CHARS
        or value.startswith("/")
        or "\\" in value
        or ENCODED_PATH_ESCAPE.search(value)
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        raise ParseFailure("relationship")
    parts: list[str] = []
    for part in value.split("/"):
        if part in {"", "."}:
            if part == "":
                raise ParseFailure("relationship")
            continue
        if part == "..":
            if not allow_parent or not parts:
                raise ParseFailure("relationship")
            parts.pop()
            continue
        if ":" in part and not parts:
            raise ParseFailure("relationship")
        parts.append(part)
    if not parts:
        raise ParseFailure("relationship")
    return "/".join(parts)


def _extract_docx(
    archive, members, main_part, parser, budget, output: Output,
) -> int:
    main = _read_tree(archive, members, main_part, parser, budget)
    if main.tag != f"{{{WORD_NS}}}document":
        raise ParseFailure("xml_parse")
    for index, text in enumerate(_word_paragraphs(main), start=1):
        output.add("paragraph", f"main#p{index}", text)
    rels = _relationships(
        archive, members, main_part, parser, budget, required=False
    )
    allowed = {
        f"{OFFICE_REL_BASE}/header",
        f"{OFFICE_REL_BASE}/footer",
        f"{OFFICE_REL_BASE}/footnotes",
        f"{OFFICE_REL_BASE}/endnotes",
        f"{OFFICE_REL_BASE}/comments",
    }
    related: list[tuple[str, str]] = []
    for relation in rels.values():
        if relation.kind not in allowed:
            continue
        if relation.external:
            raise ParseFailure("relationship")
        target = _resolve_target(main_part, relation.target)
        if not target.startswith("word/") or target not in members:
            raise ParseFailure("relationship")
        related.append((relation.kind, target))
    for _kind, target in sorted(set(related)):
        root = _read_tree(archive, members, target, parser, budget)
        for index, text in enumerate(_word_paragraphs(root), start=1):
            output.add("paragraph", f"{target}#p{index}", text)
    return 1 + len(set(related))


def _word_paragraphs(root) -> list[str]:
    paragraphs: list[str] = []

    def collect(element) -> None:
        namespace, local = _qname(element.tag)
        if namespace == MC_NS and local == "AlternateContent":
            chosen = next(
                (child for child in element
                 if _qname(child.tag) == (MC_NS, "Fallback")),
                next(iter(element), None),
            )
            if chosen is not None:
                for child in chosen:
                    collect(child)
            return
        if namespace == WORD_NS and local == "p":
            paragraphs.append(_word_paragraph_text(element))
        for child in element:
            collect(child)

    collect(root)
    return paragraphs


def _word_paragraph_text(paragraph) -> str:
    parts: list[str] = []

    def visit(element, deleted: bool = False, *, root: bool = False) -> None:
        namespace, local = _qname(element.tag)
        if namespace == WORD_NS and local == "p" and not root:
            return
        if namespace == MC_NS and local == "AlternateContent":
            chosen = next(
                (child for child in element if _qname(child.tag) == (MC_NS, "Fallback")),
                next(iter(element), None),
            )
            if chosen is not None:
                for child in chosen:
                    visit(child, deleted)
            return
        deleted = deleted or (namespace == WORD_NS and local == "del")
        if namespace == WORD_NS and local in {"t", "delText"}:
            text = _safe_text(element.text or "")
            parts.append(f"[deleted:{text}]" if deleted and text else text)
        elif namespace == DRAWING_NS and local == "t":
            parts.append(_safe_text(element.text or ""))
        elif namespace == WORD_NS and local == "tab":
            parts.append("\t")
        elif namespace == WORD_NS and local in {"br", "cr"}:
            parts.append("\n")
        for child in element:
            visit(child, deleted)

    visit(paragraph, root=True)
    return "".join(parts)


def _extract_xlsx(
    archive, members, main_part, parser, budget, output: Output,
) -> int:
    workbook = _read_tree(archive, members, main_part, parser, budget)
    if workbook.tag != f"{{{SHEET_NS}}}workbook":
        raise ParseFailure("xml_parse")
    rels = _relationships(
        archive, members, main_part, parser, budget, required=True
    )
    shared: list[str] = []
    shared_rels = [
        relation for relation in rels.values()
        if relation.kind == f"{OFFICE_REL_BASE}/sharedStrings"
    ]
    if len(shared_rels) > 1:
        raise ParseFailure("relationship")
    if shared_rels:
        relation = shared_rels[0]
        if relation.external:
            raise ParseFailure("relationship")
        target = _resolve_target(main_part, relation.target)
        shared = _shared_strings(
            _read_tree(archive, members, target, parser, budget)
        )
    sheets = [
        element for element in workbook.iter()
        if element.tag == f"{{{SHEET_NS}}}sheet"
    ]
    if len(sheets) > MAX_SHEETS:
        raise OutputLimit("sheet_limit")
    cell_count = 0
    for index, sheet in enumerate(sheets, start=1):
        name = sheet.attrib.get("name")
        rel_id = sheet.attrib.get(REL_ATTR)
        if type(name) is not str or type(rel_id) is not str or rel_id not in rels:
            raise ParseFailure("relationship")
        relation = rels[rel_id]
        if relation.external:
            raise ParseFailure("relationship")
        if relation.kind == f"{OFFICE_REL_BASE}/chartsheet":
            continue
        if relation.kind != f"{OFFICE_REL_BASE}/worksheet":
            raise ParseFailure("relationship")
        target = _resolve_target(main_part, relation.target)
        if not target.startswith("xl/") or target not in members:
            raise ParseFailure("relationship")
        root = _read_tree(archive, members, target, parser, budget)
        if root.tag != f"{{{SHEET_NS}}}worksheet":
            raise ParseFailure("xml_parse")
        cells, used = _worksheet_cells(root, shared, MAX_CELLS - cell_count)
        cell_count += used
        for reference, text in cells:
            output.add("cell", f"sheet-{index}!{reference}", text)
    return len(sheets)


def _shared_strings(root) -> list[str]:
    if root.tag != f"{{{SHEET_NS}}}sst":
        raise ParseFailure("shared_string")
    found: list[str] = []
    for child in root:
        if child.tag != f"{{{SHEET_NS}}}si":
            continue
        found.append("".join(
            _safe_text(node.text or "")
            for node in child.iter()
            if node.tag == f"{{{SHEET_NS}}}t"
        ))
        if len(found) > MAX_CELLS:
            raise OutputLimit("cell_limit")
    return found


def _worksheet_cells(
    root, shared: list[str], remaining_cells: int,
) -> tuple[list[tuple[str, str]], int]:
    cells: list[tuple[str, str]] = []
    seen: set[str] = set()
    count = 0
    for cell in root.iter(f"{{{SHEET_NS}}}c"):
        count += 1
        if count > remaining_cells:
            raise OutputLimit("cell_limit")
        reference = cell.attrib.get("r")
        if type(reference) is not str or not _valid_cell_reference(reference):
            raise ParseFailure("cell_reference")
        if reference in seen:
            raise ParseFailure("cell_reference")
        seen.add(reference)
        cell_type = cell.attrib.get("t")
        formula = _direct_text(cell, "f")
        raw_value = _direct_text(cell, "v")
        inline = next(
            (child for child in cell if child.tag == f"{{{SHEET_NS}}}is"), None
        )
        value = _cell_value(cell_type, raw_value, inline, shared)
        if formula is not None:
            formula = _safe_text(formula)
            rendered = f"formula:{formula}"
            if value is not None:
                rendered += f"\tcached:{value}"
            cells.append((reference, rendered))
        elif value is not None:
            cells.append((reference, value))
    return cells, count


def _cell_value(cell_type, raw, inline, shared: list[str]) -> str | None:
    if cell_type == "inlineStr":
        if inline is None:
            return ""
        return "".join(
            _safe_text(node.text or "")
            for node in inline.iter()
            if node.tag == f"{{{SHEET_NS}}}t"
        )
    if cell_type == "s":
        if raw is None or not raw.isascii() or not raw.isdigit():
            raise ParseFailure("shared_string")
        index = int(raw)
        if index >= len(shared):
            raise ParseFailure("shared_string")
        return shared[index]
    if raw is None:
        return None
    raw = _safe_text(raw)
    if cell_type == "b":
        if raw not in {"0", "1"}:
            raise ParseFailure("formula_value")
        return "TRUE" if raw == "1" else "FALSE"
    if cell_type == "e":
        return f"error:{raw}"
    if cell_type not in {None, "n", "str", "d"}:
        raise ParseFailure("formula_value")
    return raw


def _direct_text(element, local: str) -> str | None:
    matches = [
        child for child in element if child.tag == f"{{{SHEET_NS}}}{local}"
    ]
    if len(matches) > 1:
        raise ParseFailure("formula_value")
    if not matches:
        return None
    return _safe_text(matches[0].text or "")


def _valid_cell_reference(value: str) -> bool:
    match = CELL_REFERENCE.fullmatch(value)
    if match is None:
        return False
    column, raw_row = match.groups()
    number = 0
    for char in column:
        number = number * 26 + ord(char) - 64
    return number <= 16_384 and int(raw_row) <= 1_048_576


def _extract_pptx(
    archive, members, main_part, parser, budget, output: Output,
) -> int:
    presentation = _read_tree(archive, members, main_part, parser, budget)
    if presentation.tag != f"{{{PRESENTATION_NS}}}presentation":
        raise ParseFailure("xml_parse")
    rels = _relationships(
        archive, members, main_part, parser, budget, required=True
    )
    slides = [
        element for element in presentation.iter()
        if element.tag == f"{{{PRESENTATION_NS}}}sldId"
    ]
    if len(slides) > MAX_SLIDES:
        raise OutputLimit("slide_limit")
    for index, slide_id in enumerate(slides, start=1):
        rel_id = slide_id.attrib.get(REL_ATTR)
        if type(rel_id) is not str or rel_id not in rels:
            raise ParseFailure("relationship")
        relation = rels[rel_id]
        if relation.external or relation.kind != f"{OFFICE_REL_BASE}/slide":
            raise ParseFailure("relationship")
        target = _resolve_target(main_part, relation.target)
        if not target.startswith("ppt/slides/") or target not in members:
            raise ParseFailure("relationship")
        slide = _read_tree(archive, members, target, parser, budget)
        if slide.tag != f"{{{PRESENTATION_NS}}}sld":
            raise ParseFailure("xml_parse")
        label = f"slide-{index}"
        output.add("slide", label, _drawing_text(slide))
        slide_rels = _relationships(
            archive, members, target, parser, budget, required=False
        )
        for kind, suffix in (("notes", "notesSlide"), ("comments", "comments")):
            related = [
                item for item in slide_rels.values()
                if item.kind == f"{OFFICE_REL_BASE}/{suffix}"
            ]
            if len(related) > 1:
                raise ParseFailure("relationship")
            if not related:
                continue
            item = related[0]
            if item.external:
                raise ParseFailure("relationship")
            part = _resolve_target(target, item.target)
            if not part.startswith("ppt/") or part not in members:
                raise ParseFailure("relationship")
            root = _read_tree(archive, members, part, parser, budget)
            text = _drawing_text(root) if kind == "notes" else _comment_text(root)
            output.add(kind, f"{label}-{kind}", text)
    return len(slides)


def _drawing_text(root) -> str:
    parts: list[str] = []

    def visit(element) -> None:
        namespace, local = _qname(element.tag)
        if namespace == MC_NS and local == "AlternateContent":
            chosen = next(
                (child for child in element if _qname(child.tag) == (MC_NS, "Fallback")),
                next(iter(element), None),
            )
            if chosen is not None:
                for child in chosen:
                    visit(child)
            return
        if namespace == DRAWING_NS and local == "t":
            parts.append(_safe_text(element.text or ""))
        for child in element:
            visit(child)
        if namespace == DRAWING_NS and local == "p":
            parts.append("\n")

    visit(root)
    return "".join(parts).rstrip("\n")


def _comment_text(root) -> str:
    parts = [
        _safe_text(element.text or "")
        for element in root.iter()
        if _qname(element.tag)[1] in {"t", "text"}
        and _qname(element.tag)[0] in {PRESENTATION_NS, DRAWING_NS}
    ]
    return "\n".join(text for text in parts if text)


def _qname(value: object) -> tuple[str, str]:
    if type(value) is not str or not value.startswith("{") or "}" not in value:
        raise ParseFailure("xml_parse")
    namespace, local = value[1:].split("}", 1)
    if not namespace or not local:
        raise ParseFailure("xml_parse")
    return namespace, local


def _extract(parser, max_bytes: int, max_chars: int):
    budget = XmlBudget()
    output = Output(max_bytes, max_chars)
    with zipfile.ZipFile(INPUT_PATH, "r") as archive:
        members, expanded = _inspect_archive(archive)
        format_name, main_part = _identify_package(
            archive, members, parser, budget
        )
        if format_name == "docx":
            primary_count = _extract_docx(
                archive, members, main_part, parser, budget, output
            )
        elif format_name == "xlsx":
            primary_count = _extract_xlsx(
                archive, members, main_part, parser, budget, output
            )
        elif format_name == "pptx":
            primary_count = _extract_pptx(
                archive, members, main_part, parser, budget, output
            )
        else:
            raise ParseFailure("content_types")
        output.primary_unit_count = primary_count
    return format_name, output, len(members), expanded


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return 64
    try:
        max_bytes, max_chars = map(_positive, argv)
    except (TypeError, ValueError):
        return 64
    parser = _load_xml_parser()
    if parser is None:
        return 0
    try:
        format_name, output, member_count, expanded = _extract(
            parser, max_bytes, max_chars
        )
        _write_frame(
            "success",
            format_name=format_name,
            output=output,
            member_count=member_count,
            expanded_bytes=expanded,
        )
    except UnsupportedFormat as exc:
        _write_frame("unsupported_format", detail=exc.detail)
    except OutputLimit as exc:
        _write_frame("parser_output_limit", detail=exc.detail)
    except ParseFailure as exc:
        _write_frame("parse_error", detail=exc.detail)
    except MemoryError:
        _write_frame("parse_oom", detail="memory_limit")
    except zipfile.BadZipFile:
        _write_frame("parse_error", detail="archive_corrupt")
    except Exception:
        _write_frame("parse_error", detail="archive_corrupt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
