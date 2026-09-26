# C7 — Sandboxed legacy Office extraction

Date: 2026-08-16
Status: **Complete — sandboxed legacy `.doc` and `.xls` extraction**

## Issue

Analyst identifies Compound File Binary (CFB/OLE) documents only as unsupported binary
data. That leaves legacy Word and Excel documents outside extraction coverage, while
several historically common converters have unsafe parser histories.

## Root cause

C3 supplies containment, but the durable router has no CFB candidate lane, authenticated
legacy parser, strict IPC contract or provenance model. A CFB signature alone does not
distinguish Word, Excel, PowerPoint or an arbitrary compound file.

## C7A scope — legacy Word

- Route the exact CFB signature as a `legacy_office` candidate without consulting the
  filename extension.
- Require the system `/usr/bin/antiword` package at exact Debian revision `0.37-17` and
  upstream version `0.37`. Older or unverifiable packages fail preflight. Revision 17
  removes a known out-of-bounds document-summary parser.
- Run Antiword only inside C3 with a fixed command, fixed input path, private HOME/tmp,
  network disabled and a narrow runtime containing its binary, UTF-8 map and libc.
- Include revision-deleted and hidden text for audit recall. Never use stdin, because
  Antiword copies stdin to an internal temporary file.
- Capture stdout and stderr concurrently under hard limits. Any nonzero exit, warning,
  signal, timeout or overflow discards partial text and returns a closed outcome.
- Preserve only honest `output-line-N` provenance. Antiword does not expose reliable
  source paragraph identities.
- Validate exact versions, status/detail vocabulary, UTF-8, control characters, units,
  labels, counts, delimiters and body length again in the durable process.

## C7A outcome

Legacy Word extraction now succeeds inside the strict sandbox without relying on a
`.doc` extension. A generic CFB or Excel workbook is not silently labelled Word:
Antiword must authenticate supported Word content through a successful parse. Unsupported,
encrypted, malformed and resource-limit outcomes retain no partial text.

The installed Antiword mapping emits supplementary Unicode characters as paired CESU-8
surrogates. The child first requires strict UTF-8, then narrowly accepts only a complete
high-plus-low surrogate pair and converts it to one Unicode scalar. Unpaired, reversed,
truncated or otherwise invalid bytes still fail; there is no replacement decoding.

## C7B scope — legacy Excel

The technically small `xlrd` option is not being added silently. Its exact 2.0.2 licence
retains the original BSD advertising clause, which GNU classifies as GPL-incompatible.
LibreOffice avoids that licence conflict but adds a much larger mutable native parser and
conversion surface.

E12 records the HI's acceptance of `python-calamine==0.8.2`: MIT licensed, an attested
2.25 MiB native wheel, a narrow shared-library closure and successful C3/public-XLS
smokes. The first approved artifact is exact CPython 3.14 / Linux x86-64 only. Other
platforms fail closed until their wheels and native closures are independently reviewed.

- Keep Antiword first for a CFB candidate. Only its exact authenticated non-Word outcome
  falls through to the independent XLS sandbox; DOC success and all other DOC terminals
  remain final.
- Bind only the exact `python_calamine` initializer, native extension, child/contract and
  measured shared-library closure. Recheck host identities and hashes before launch; the
  child hashes its two package files again before import.
- Read through one fixed private `.xls` alias so encrypted workbooks authenticate
  correctly. No workbook content is copied or written.
- Include visible, hidden and very-hidden worksheets. Skip macro, chart, dialog and VBA
  sheets without executing them. Detect empty worksheets before calling the affected
  0.8.2 iterator.
- Preserve exact workbook-order `sheet-N!A1` cell provenance and scalar types. Apply hard
  sheet, dense-cell, emitted-cell, cell-text, aggregate-text and frame limits.
- Return cached formula results only. Never expose formula source, recalculate formulas or
  imply that cached values are current. Blank/error cells may collapse to empty; temporal
  values use explicit deterministic representations after calamine's conversion.
- Revalidate the complete status/detail vocabulary, package/engine versions, scalar
  grammar, provenance order, counts, delimiters, UTF-8 and controls in the durable process.

## C7B outcome

Legacy XLS now authenticates and extracts inside its own strict C3 sandbox without
trusting extensions. The public workbook produced nine typed cell units with exact
sheet/cell provenance. A legacy Word file still finishes in the Antiword lane; arbitrary
CFB content is not silently labelled Word or Excel. Missing optional dependencies,
encryption, malformed workbooks, native failure and resource limits all fail closed with
no partial cell text.

## Validation

- C7A/C7B/purity regression — PASS, 204 tests. This includes a live sandboxed public DOC,
  a generated extensionless public XLS and an offline hash-attested upstream encrypted
  XLS fixture through the real C3 boundary.
- Focused installer plus C3–C7 hostile/purity regression — PASS, 356 tests.
- Exact optional dependency check — PASS for PyMuPDF/MuPDF 1.28.0/1.28.0,
  defusedxml 0.7.1 and python-calamine 0.8.2.
- Production compilation and `git diff --check` — PASS.
- Public privacy check — PASS across the exact 21-path C7B delta and 262 public model
  responses. Eight pre-existing keyboard-control paths remained unchanged. The existing
  `RESEARCH_NOTES.md` private-corpus self-reference was absent from this task's diff. The
  exact upstream wheel SBOM contains only its public GitHub Actions build path; that
  reviewed vendor field is the sole scanner exemption.
- Production sizes: `extract.py` 1,123, `xls_child.py` 437, `xls_frame.py` 339,
  `xls_contract.py` 33 and the controlled installer 376 lines. All are at or below the
  1,200-line excellent threshold. The 1,210-line C7B test module is covered by the HI's
  explicit test-only size exemption.
- Independent hostile review — PASS; no blocker remains.
- No private document was read. Private Stage E remains deferred.

## Sources

- Antiword 0.37-17 manual and supported formats:
  https://manpages.debian.org/testing/antiword/antiword.1.en.html
- Debian bug 968812 and the 0.37-17 fix:
  https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=968812
- Debian 0.37-17 signed source upload:
  https://tracker.debian.org/news/1640294/accepted-antiword-037-17-source-into-unstable/
- Microsoft Compound File Binary format:
  https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/53989ce4-7b05-4f8d-829b-d08d6148375b
- python-calamine 0.8.2 release and files:
  https://pypi.org/project/python-calamine/0.8.2/
- python-calamine 0.8.2 licence and dependency lock:
  https://github.com/dimastbk/python-calamine/blob/v0.8.2/LICENSE
  and https://github.com/dimastbk/python-calamine/blob/v0.8.2/Cargo.lock
- GNU licence compatibility notes for the original BSD advertising clause:
  https://www.gnu.org/licenses/license-list.html#OriginalBSD
