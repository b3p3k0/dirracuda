# C7 — Sandboxed legacy Office extraction

Date: 2026-08-15
Status: **C7A `.doc` complete; C7B `.xls` parser decision pending**

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

## C7B gate — legacy Excel

The technically small `xlrd` option is not being added silently. Its exact 2.0.2 licence
retains the original BSD advertising clause, which GNU classifies as GPL-incompatible.
LibreOffice avoids that licence conflict but adds a much larger mutable native parser and
conversion surface.

The measured recommendation is `python-calamine==0.8.2`: MIT licensed, an attested
2.25 MiB native wheel, a narrow shared-library closure and successful C3/public-XLS
smokes. It returns cached formula values without calculation and exposes hidden sheets.
Its accepted costs would be an exact per-Python/architecture wheel allowlist, Rust-native
parser containment, automatic temporal coercion and explicit handling of its empty-sheet
iterator panic. Changing the frozen xlrd/LibreOffice choice requires an accepted erratum
before implementation.

## Validation

- `./venv/bin/python -m pytest -q shared/tests/test_analyst_c7a.py` — PASS,
  55 tests, including a live sandboxed public DOC generated in an isolated temporary
  profile.
- Focused C3–C7/purity/installer regression — PASS, 213 tests.
- Production compilation and `git diff --check` — PASS.
- Public privacy check — PASS across the exact 13-path C7A delta and 262 public model
  responses. Eight pre-existing keyboard-control paths remained unchanged. The one
  `RESEARCH_NOTES.md` private-corpus self-reference was byte-identical to `HEAD`; the
  new diff contained no privacy hit.
- Production sizes: `extract.py` 841, `legacy_child.py` 282,
  `legacy_frame.py` 230, `formats.py` 100 and `legacy_contract.py` 21 lines. All are
  below 1,200 lines.
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
