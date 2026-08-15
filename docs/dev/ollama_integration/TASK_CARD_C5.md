# C5 — Sandboxed PDF text extraction

Date: 2026-08-14
Status: **Complete**

## Issue

Analyst can safely extract RTF and plain text, but PDF candidates still stop as
unsupported. PDF parsing introduces a native parser with an RCE-class history, scanned
documents without a text layer, encrypted inputs and an optional AGPL dependency that
must not affect core startup.

## Root cause

C3 established containment and C4 established bounded routing/IPC, but no production PDF
child or narrowly bound PyMuPDF runtime exists. C0B selected the exact dependency from a
sandboxed probe; it did not validate production extraction behavior.

## Scope

- Add a controlled installer that hash-verifies exact PyMuPDF and MuPDF 1.28.0 source
  releases plus build wheels, then builds offline with OCR disabled; do not consume the
  provenance-mismatched published x86_64 wheel or an unverified nested download.
- Route `%PDF-` magic to a standalone child that imports PyMuPDF only inside C3.
- Bind only the exact PyMuPDF package and its direct native dependencies, never the full
  virtual environment, repository, HOME or `/usr` tree.
- Assert PyMuPDF `1.28.0` and embedded MuPDF `1.28.0` inside every child before parsing.
- Extract plain page text deterministically with `get_text("text", sort=True)` and no OCR.
- Return distinct closed outcomes for encrypted PDFs, no text layer, malformed PDFs,
  page/output limits, missing or mismatched dependencies and supervisor failures.
- Carry bounded page counts and parser versions through a strict PDF-specific IPC frame.
- Record PyMuPDF/MuPDF attribution and AGPL source/licence expectations when introducing
  the dependency.

## Limits

- Source size: nonzero and at most 100 MiB.
- Page count: at most 10,000 before page iteration.
- Extracted UTF-8: at most 8 MiB and 8 million Unicode code points.
- One PDF per sandbox child; C3 CPU, wall, task, address-space, open-file and pipe limits
  remain authoritative.

## Compatibility and fallback

- Core Dirracuda and package import remain functional when PyMuPDF is absent.
- A missing or version-mismatched optional dependency fails closed as
  `sandbox_unavailable`; it never falls back to an unpinned parser.
- Passwords are not requested, guessed or persisted. Protected PDFs are `encrypted`.
- `Document.needs_pass` is the access boundary. Advisory copy-permission flags on a PDF
  that opens without a password do not independently block this local audit workflow.
- A PDF with zero non-whitespace extracted text is `no_text_layer`, not success.
- Private Stage E remains deferred; validation uses synthetic/public PDFs only.

## Out of scope

OCR, pdfplumber fallback/table reconstruction, OOXML, legacy Office, persistence,
report/UI wiring and private documents.

## Acceptance

1. PDF magic routes to PDF and known non-PDF binary magic remains unsupported.
2. Public live-sandbox fixtures prove multi-page text, reading order, encrypted,
   no-text-layer and malformed outcomes.
3. Version mismatch, missing dependency, page/output overflow and malformed IPC fail
   closed without partial text.
4. The durable process never imports PyMuPDF or the child parser.
5. Runtime binds are exact and exclude the virtual-environment root, HOME, repository
   root and broad `/usr`.
6. Optional-dependency install, focused tests, privacy scan, file-size check, licence
   notice and root README review pass.

## Outcome

C5 adds magic-routed PDF extraction through one standalone PyMuPDF child inside C3. The
durable worker binds only the exact package tree, native runtime closure and child file;
it never imports PyMuPDF. The PDF frame preserves page provenance with independently
validated per-page character counts and returns explicit `encrypted`, `no_text_layer`,
parse and output-limit outcomes without partial text.

E10 replaces the C0B wheel as a production artifact: the optional lane forbids wheels,
and its controlled installer separately hash-verifies the official PyMuPDF and MuPDF
1.28.0 sources plus build wheels before an offline, no-OCR build. The resulting runtime
reports PyMuPDF/MuPDF 1.28.0/1.28.0.
Attribution and the complete AGPLv3 text are recorded under `licenses/`. Validation used
only synthetic/public PDFs; private Stage E remains deferred.

The source build needs a working C/C++ toolchain and takes materially longer than a wheel
install. The eventual Analyst installation/troubleshooting guide must state that clearly.
The C13 network-release gate must preserve or host both exact dependency source archives
and link an exact public Dirracuda source identity; development links alone are not a
release source offer.

## Validation

- Controlled-install tests verify every frozen artifact, unsafe-archive rejection,
  offline build configuration and disabled OCR. The real controlled source build
  verified both source hashes and reported PyMuPDF/MuPDF 1.28.0/1.28.0.
- `./venv/bin/python -m pytest -q scripts/tests/test_install_analyst_deps.py
  shared/tests/test_analyst_c1.py shared/tests/test_analyst_c2.py
  shared/tests/test_analyst_c3.py shared/tests/test_analyst_c4.py
  shared/tests/test_analyst_c5.py shared/tests/test_analyst_purity.py
  shared/tests/test_analyst_container_cases.py` — PASS, 177 tests.
- `./venv/bin/python -m pytest -q` — wider regression was capped after 11 minutes:
  3,191 passed and 6 skipped. The known pre-existing Web UI/Tkinter import test failed;
  the operator interrupt then deliberately drove the active C0B-2 runtime test into its
  cancellation state, so that second displayed failure is not a product regression.
- Public privacy scan with the genuine pre-C5 baseline, exact 16-path C5 allowlist and
  262 raw public benchmark responses — PASS, no content hits. The unchanged untracked
  keyboard-control documentation remained outside the task delta.
- `git diff --check` and production Python compilation — PASS.
- Production files are all below 1,200 lines. Root `README.md` was reviewed and updated
  with the optional AGPL component notice.

## Sources

- PyMuPDF 1.28 version/API documentation:
  https://pymupdf.readthedocs.io/en/latest/version.html
- PyMuPDF version constants:
  https://pymupdf.readthedocs.io/en/latest/vars.html
- PyMuPDF text extraction and reading order:
  https://pymupdf.readthedocs.io/en/latest/app1.html
- PyMuPDF encryption indicators:
  https://pymupdf.readthedocs.io/en/latest/document.html
- MuPDF CVE list:
  https://mupdf.com/releases/cve
- PyMuPDF package and AGPL licensing statement:
  https://pypi.org/project/PyMuPDF/1.28.0/
- GNU AGPL v3 section 13 and GPL compatibility:
  https://www.gnu.org/licenses/agpl-3.0.html
  https://www.gnu.org/licenses/gpl-faq.html
