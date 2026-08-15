# C6 — Sandboxed OOXML text extraction

Date: 2026-08-15
Status: **Complete**

## Issue

Analyst routes ZIP signatures as unsupported binary data, so DOCX, XLSX and PPTX files
cannot reach deterministic detectors. OOXML is a relationship-driven ZIP package, not a
trusted filename extension: unsafe handling can expose the worker to decompression bombs,
ambiguous paths, external relationships and hostile XML.

## Root cause

C3 supplies containment and C4/C5 supply strict extraction IPC, but there is no OOXML
candidate route, package authenticator, archive gate, defused XML child or semantic-unit
provenance contract.

## Scope

- Route all three ZIP signatures to an `ooxml` candidate. Inside C3, require
  `[Content_Types].xml`, `_rels/.rels`, exactly one internal `officeDocument`
  relationship and exactly one matching non-macro main part.
- Support non-macro Transitional DOCX, XLSX and PPTX. Return generic ZIP, macro-enabled
  packages and Strict OOXML as explicit unsupported outcomes.
- Inspect every central-directory member before parsing any XML. Never extract a member
  to disk, recursively walk a general relationship graph or decompress media.
- Parse selected XML only with `defusedxml==0.7.1`, explicitly setting
  `forbid_dtd=True`, `forbid_entities=True` and `forbid_external=True` on every call.
- Preserve paragraph, sheet/cell, slide and notes provenance in a dedicated strict OOXML
  frame. The durable worker independently revalidates the complete frame.
- Extend the controlled dependency installer and PSF licence notices without making the
  optional dependency part of core startup.

## Frozen limits

- Source: existing nonzero maximum of 100 MiB.
- Archive metadata: at most 1,000 members, 128 MiB per declared member, 256 MiB total
  declared expansion and 100:1 per-member/aggregate ratio under E11. Exact limits pass;
  limit + 1 fails. Unsupported media is never decompressed.
- Parsed XML: 8 MiB per part, 16 MiB total, depth 128, 100,000 elements per part,
  250,000 elements total, 64 attributes per element and 16,384 characters per attribute.
- Semantics: 50,000 nonempty provenance units, 256 sheets, 250,000 declared cells and
  1,000 slides. Existing 8 MiB/8-million-character text limits remain authoritative;
  an 8 MiB frame-header cap fails closed rather than truncating provenance.

## Extraction contract

- **DOCX:** main story plus internally linked headers, footers, footnotes, endnotes and
  comments. Emit each nonempty paragraph as an exact provenance unit; preserve tabs,
  breaks and retained deleted text. Ignore styles,
  themes, external hyperlinks, `altChunk`, media and embedded objects.
- **XLSX:** workbook sheet order, shared strings, inline strings and lexical scalar
  values. Emit formula text plus its stored cached value; never calculate formulas or
  infer dates from styles. Each nonempty cell is an exact sheet-index/cell-reference
  provenance unit.
- **PPTX:** presentation slide order plus internally linked notes/comments. Extract
  DrawingML text; omit masters, layouts, themes, charts, images, SmartArt and embedded
  objects.
- Blank valid packages are successful empty extraction, not parser failure. Unsupported
  coverage remains visible in the documented omissions.

## Acceptance

1. ZIP magic is only a candidate; package content and root relationships determine the
   exact format without consulting the filename.
2. Every archive gate runs before XML. Paths, duplicates, encryption, special entries,
   unsupported compression, size and ratio limits fail closed without partial text.
3. Public synthetic live-sandbox fixtures prove DOCX stories, XLSX cell/formula
   semantics, PPTX slide order/notes, blank documents, DTD/XXE rejection and provenance.
4. Strict IPC rejects malformed schemas, duplicate keys, type coercion, bad UTF-8,
   controls, wrong versions, delimiters and count mismatches.
5. Core and durable imports remain parser-free when defusedxml is absent. Runtime binds
   exclude the venv root, repository, HOME, broad `/usr`, PyMuPDF and the C4 child.
6. Controlled dependency install, focused tests, public privacy scan, file sizes,
   licence notice and root README review pass.

## Out of scope

Strict OOXML, VBA/macros, password-protected OOXML, formula calculation, date/style
interpretation, charts, images, OCR, SmartArt, embedded packages/OLE, recursive general
relationship traversal, persistence, report/UI wiring and private documents.

## Outcome

C6 routes ZIP signatures only as OOXML candidates, authenticates one non-macro
Transitional DOCX/XLSX/PPTX package inside the C3 sandbox and extracts bounded text
without using filename extensions. DOCX paragraphs, XLSX cells and PPTX slide/notes/
comment content are separate provenance units in a strict frame that the durable worker
revalidates independently. Generic ZIP, macro, Strict, malformed, encrypted, hostile XML
and resource-limit cases fail closed without partial text.

The optional lane now installs exact `defusedxml==0.7.1` alongside the controlled PDF
build. Runtime binds expose only that pure-Python package and the two exact OOXML child
files. E11 explicitly separates the 256 MiB declared-package inventory allowance from
the 16 MiB total XML actually decompressed and parsed; unsupported media is never opened.
Only synthetic/public packages were used. Private Stage E remains deferred.

## Validation

- The real controlled installer rebuilt PyMuPDF/MuPDF and installed defusedxml from the
  exact verified artifact — PASS, versions `1.28.0` / `1.28.0` / `0.7.1`.
- `./venv/bin/python -m pytest -q scripts/tests/test_install_analyst_deps.py
  shared/tests/test_analyst_c1.py shared/tests/test_analyst_c2.py
  shared/tests/test_analyst_c3.py shared/tests/test_analyst_c4.py
  shared/tests/test_analyst_c5.py shared/tests/test_analyst_c6.py
  shared/tests/test_analyst_c6_hostile.py shared/tests/test_analyst_purity.py
  shared/tests/test_analyst_container_cases.py` — PASS, 220 tests.
- The public privacy scan used the genuine pre-C6 owner-only baseline, the exact 23-path
  C6 allowlist and 262 raw public benchmark responses — PASS, no content hits. The eight
  pre-existing keyboard-control docs remained unchanged and outside the task delta.
- The wider `pytest -k analyst` run was capped after 10:38: 773 passed and 6 skipped.
  Its two displayed source-identity failures were invalid because this card's files
  changed while those tests were running; a stable-tree rerun was capped after 12:22
  before the first expensive public-flow test completed. No wider result is claimed.
- Production compilation, `git diff --check`, exact licence digest, dependency check and
  root README review — PASS. All touched production Python files remain below 1,200
  lines; the largest is the 990-line sandbox-only OOXML child.

## Sources

- defusedxml 0.7.1 release and digest:
  https://pypi.org/pypi/defusedxml/0.7.1/json
- defusedxml parser controls and XML hardening guidance:
  https://github.com/tiran/defusedxml/tree/v0.7.1
- Python ZIP security and decompression guidance:
  https://docs.python.org/3/library/zipfile.html
- ECMA-376 Office Open XML / Open Packaging Conventions:
  https://ecma-international.org/publications-and-standards/standards/ecma-376/
- Microsoft WordprocessingML structure:
  https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document
- Microsoft SpreadsheetML structure:
  https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document
- Microsoft PresentationML structure:
  https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document
