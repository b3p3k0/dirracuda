# C4 — Sandboxed RTF and plain-text extraction

Date: 2026-08-14
Status: **Complete**

## Issue

Analyst has a strict parser supervisor but no production format worker. RTF is roughly
61% of the measured candidate corpus, and plain text is the smallest useful second
format. Both need bounded extraction without importing parser code into the durable
worker or introducing an optional dependency prematurely.

## Root cause

C1–C3 deliberately stopped at pure analysis contracts, safe inventory and containment.
The historical benchmark decoder is not a production parser, and extension-based
routing would send mislabeled hostile files to the wrong implementation.

## Scope

- Add a pure 4 KiB magic/text sniffer. Extension is never authoritative.
- Add one standalone child script for RTF and plain text. Production orchestration
  executes the file inside C3 and never imports it.
- Implement the Microsoft RTF 1.9.1 text controls needed for safe extraction: groups,
  escaped literals/hex bytes, code pages, Unicode + fallback counts, binary skipping,
  ignored destinations, paragraph/line/tab and common textual control symbols.
- Enforce group depth, control-token, source-byte, decoded-byte and decoded-character
  limits. Malformed input returns a closed `parse_error`; partial text is discarded.
- Plain text accepts strict UTF-8 (with or without BOM), BOM-declared UTF-16/UTF-32 and
  bounded Windows-1252 fallback only when the decoded control-character heuristic passes.
- Run the child with an exact Python executable, standard-library tree, direct shared
  libraries and child-file bind list. Never bind the repository, HOME or `/usr` as a
  production runtime tree.
- Use a strict framed IPC envelope. Validate all fields, lengths, UTF-8 and reason enums;
  discard stdout/stderr on every non-success.
- Preserve the C3 source fingerprint before and after extraction.

## Limits

- Source size: nonzero and at most 100 MiB, matching the frozen production default.
- Extracted UTF-8: at most 8 MiB and 8 million Unicode code points.
- RTF group depth: at most 256; control word: at most 64 ASCII letters; numeric parameter:
  at most 16 characters.
- C3 wall/CPU/task/address-space/open-file/output limits remain authoritative outside
  these parser-local bounds.

## Compatibility and fallback

- No requirements file changes. C4 uses only the Python standard library.
- Unknown/binary magic is `unsupported_format`; empty and oversize remain distinct.
- An unsupported RTF code page or malformed Unicode/control structure is `parse_error`,
  never silently replacement-decoded.
- Private Stage E stays deferred; only synthetic/public fixtures are used.
- E9 corrects the future sealed-`memfd` handoff to `--ro-bind-data`. Named C2 sources
  continue using `--ro-bind-fd` in C4.

## Out of scope

PDF, OOXML, legacy Office, optional dependencies, persistent sidecar state, report/UI
wiring, reduced isolation and private documents.

## Acceptance

1. Magic beats extension; known PDF/ZIP/OLE/executable/image/compressed signatures never
   reach the text decoder.
2. Public RTF fixtures cover escaped text, code pages, Unicode/surrogates, fallback,
   destinations, binary data, nesting and malformed/limit boundaries.
3. Plain-text fixtures cover every accepted encoding plus NUL/control/binary rejection.
4. Live C3 runs prove RTF and text extraction with no network/HOME/repository visibility.
5. Malformed, overflow, timeout/cancel and source mutation return exact closed outcomes
   and expose no partial content.
6. The durable modules do not import the child parser or optional parser libraries.
7. Focused tests, privacy scan, production file sizes and root README review pass.

## Outcome

C4 shipped a pure magic sniffer, a durable-side sandbox adapter and one standalone
standard-library parser child. The child handles RTF group/destination structure,
Unicode/fallback pairs, font-specific single- and multibyte code pages, escaped/binary
data and common text controls. Plain text handles strict UTF-8, BOM-declared UTF-16/32
and guarded Windows-1252 fallback.

The production runtime bind list contains one exact Python executable, its standard
library, direct shared-library files, `prlimit` and the one child file. It contains no
HOME, repository root or broad `/usr` bind. All failure frames discard partial content.
E9 is also implemented and live-tested: a fully sealed synthetic `memfd` reaches the
sandbox through `--ro-bind-data` as a read-only file without changing the caller's FD
offset.

No dependency, schema, migration, auth or CI file changed. No private document was read.

## Sources

- Microsoft RTF 1.9.1 normative reference:
  https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-ppt/1b008e61-9590-4dfa-ae35-7d70c2f6f46e
- Microsoft plain-text extraction behavior for RTF:
  https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxrtfex/205e1abf-b794-4fd0-b1e4-5210882233ab
- Bubblewrap FD/data CLI contract:
  https://github.com/containers/bubblewrap/blob/main/bubblewrap.c
- Python codec behavior:
  https://docs.python.org/3/library/codecs.html
