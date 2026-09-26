# Ollama Integration — Research Notes

Date: 2026-08-16
All findings verified against sources through this date. Re-check before implementation;
this field moves fast.

## Initial Local Stack (historical, measured on kevin-pc, 2026-08-04)

| Item | Value |
|------|-------|
| Ollama server version | 0.32.5 (host `ollama` CLI is not on `PATH`) |
| Verified endpoint | `http://127.0.0.1:11434`; listener was `*:11434` |
| GPU | RTX 4060 Ti, 16 GB |
| System RAM | 121 GB |
| Cores | 24 |
| Models installed | 22 |
| `OLLAMA_CONTEXT_LENGTH` | 16384 |
| `OLLAMA_KV_CACHE_TYPE` | q8_0 |
| `OLLAMA_FLASH_ATTENTION` | 1 |
| `OLLAMA_HOST` | `0.0.0.0:11434` (listening on `*`, no auth) |
| `OLLAMA_NO_CLOUD` | **not set** |

## Hardened Deployment Recheck (measured 2026-08-16)

| Item | Value |
|------|-------|
| Ollama server version | 0.32.5 |
| Container image | `ollama/ollama@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131` |
| `OLLAMA_HOST` | `127.0.0.1:11434`; loopback listener only |
| `OLLAMA_NO_CLOUD` | `1`; daemon log confirms cloud disabled and worker is offline |
| `OLLAMA_CONTEXT_LENGTH` | 64000 |
| `OLLAMA_KV_CACHE_TYPE` | q8_0 |
| `OLLAMA_FLASH_ATTENTION` | 1 |

The listener and cloud settings were corrected from the unsafe initial state before C9
live acceptance. Proxy-cleared control requests to the host's LAN, Tailscale, VPN and
container-facing addresses all failed; loopback returned HTTP 200 and Ollama 0.32.5.
These deployment checks used container metadata and `/api/version` only; they did not
perform inference or read private data. The local API has no application authentication,
so raw port 11434 remains loopback-only for MVP.

Reference implementation already on the box: `~/openwebui/scripts/ask-local.sh`
posts to `/api/chat` with `stream:false`, `options.temperature`, and an optional
JSON schema in `format`. It is a useful schema-call reference, but Analyst must
use `stream:true` with a cancel-checked read loop and socket deadlines.

## Ollama Structured Outputs

Source: https://docs.ollama.com/capabilities/structured-outputs

- `format` accepts either `"json"` or a full JSON Schema object.
- Vendor guidance: set temperature to 0, and also pass the schema as text in the
  prompt to ground the response.
- **Adherence is not guaranteed at generation time.** The docs direct callers to
  validate with `model_validate_json()` (Pydantic) or equivalent. Third-party
  write-ups claim grammar-constrained decoding makes malformed JSON
  "mechanically impossible"; the vendor doc does not make that claim. Treat
  syntactic validity as likely and semantic completeness as unverified —
  truncation and schema-shaped nonsense both remain possible.
- Ollama Cloud does not support structured outputs. A cloud-routed request would
  fail a schema call rather than silently succeed. Useful backstop, not a control.

## Cloud Routing

Ollama 0.32 can offload explicitly selected Cloud models through Ollama's cloud
service. Current official examples use tags ending in `-cloud`; Ollama source also
retains legacy `:cloud` handling. `OLLAMA_NO_CLOUD=1` disables cloud features.
Analyst rejects both known tag forms and, more importantly, accepts only the
benchmarked local tag+digest. Naming checks are a backstop, not proof of locality.

- https://docs.ollama.com/cloud
- https://docs.ollama.com/api/authentication
- https://github.com/ollama/ollama/blob/main/cmd/cmd.go

## Model Deprecation (Ollama 0.32.0)

Deprecation warnings now cover CodeLlama, Qwen2.5(-coder), Llama 3.x, Mistral,
StarCoder, and base DeepSeek-R1 — most of the installed lineup, including
`qwen2.5:14b`, the `ask-local.sh` default.

Source: https://github.com/ollama/ollama/releases/tag/v0.32.0

## Model Candidates

| Model | Size | Why |
|-------|------|-----|
| `gpt-oss:20b` | 13 GB | Fits fully in 16 GB VRAM. Noted as fastest prompt processing in the local eval notes; prompt processing is this workload's bottleneck. Not deprecated. |
| `qwen3.6:35b` (A3B MoE) | 23.9 GB installed | Apache 2.0, 256K-class context, ~3B active params. Community reports ~120 tok/s at Q4 on a 24 GB card; expect partial offload here, mitigated by MoE sparsity and 121 GB RAM. |
| `qwen3.6:27b` (dense) | 17.4 GB installed | Higher quality, all params active, will offload on 16 GB. Quality ceiling for spot checks. |

Qwen3.6 sources:
- https://insiderllm.com/guides/qwen-3-6-local-ai-guide/
- https://benchlm.ai/compare/qwen3-6-27b-vs-qwen3-6-35b-a3b

Note: Qwen3.6 is a hybrid-thinking family. Thinking must be disabled for batch
extraction — reasoning traces are pure cost across thousands of schema-constrained
calls. The local README records `qwen3:14b` already being tested with
`think=false`.

## Live Corpus Profile (`~/Documents/Extracted`, measured 2026-08-04)

**116,138 files / 295 GB across 39 host directories.**

### Format distribution

| Ext | Count | Parser family |
|-----|-------|---------------|
| rtf | 44,674 | RTF markup — trivial, text-only |
| jpg | 16,423 | image — no text layer |
| rpm | 10,965 | Linux packages — not documents |
| xml | 10,385 | text |
| doc | 5,438 | **OLE2/CFB binary** |
| pdf | 4,544 | PDF |
| txt | 4,531 | plain text |
| xls | 2,639 | **OLE2/CFB binary** |
| zip | 1,046 | archive |
| docx | 918 | OOXML |
| xlsx | 189 | OOXML |

Enumerated V1 candidate documents (rtf/xml/doc/pdf/txt/xls/docx/xlsx):
**~73,300**, about 63% of the corpus. The remainder is media, packages, archives,
and other binaries.

### Three findings that break the original scope assumption

1. **RTF is the corpus, not an edge case.** At 44,674 files it is about 61% of
   the enumerated V1 candidate documents — roughly 8x the entire OOXML + PDF
   bucket combined. A modern-Office-and-PDF-only V1 would have covered roughly
   8% of the enumerated candidate set.
2. **Legacy binary formats outnumber modern ones 9:1.** `.doc` + `.xls` = 8,077
   files versus 1,107 for `.docx` + `.xlsx`. These are OLE2/CFB compound files;
   `python-docx` and `openpyxl` cannot open them at all. They are also the
   classic macro-malware container.
3. **Host distribution is extremely long-tailed.** One host
   (`104.136.36.202 - Florida Medical FMIC`) holds 46,724 candidate documents —
   including 44,622 of the 44,674 RTFs. The next largest host has 3,804. Most
   hosts have a few hundred. Per-host reporting must survive a 47,000-document
   host and a 50-document host with the same code path.

### Size distribution

| Bucket | Count | Total | p50 | p90 | p99 | max |
|--------|-------|-------|-----|-----|-----|-----|
| RTF (FMIC) | 44,622 | 1.67 GB | 3 KB | 147 KB | — | 9.7 MB |
| pdf/doc/docx/xls/xlsx/txt | 18,259 | 7.0 GB | 41 KB | 675 KB | 4.5 MB | **502 MB** |

RTF median of 3 KB means most RTF files are a single chunk — cheap. The 502 MB
outlier and the zero-byte files (RTF min = 0) both need pre-parse gates.

### PDF text-layer rate — measured

Random sample of 120 PDFs under 20 MB, `pdftotext` with a 20 s timeout,
threshold 200 alphanumeric characters:

```
text_layer=83  no_text=37  parse_failed=0  timed_out=0
```

**69% have an extractable text layer. 31% do not** — they are scanned images.
Extrapolated, roughly 1,400 of 4,544 PDFs are unreadable without OCR. In medical
and financial corpora, scanned documents skew toward the most sensitive material
(signed forms, identity documents, cheques), so this gap is not evenly
distributed across risk. It is a measured, quantified v1 limitation, not an
unknown.

## Text Extraction — Benchmarked Landscape

| Tool | Speed | Notes |
|------|-------|-------|
| PyMuPDF | ~0.015 s/page (~66 pages/s) | Fastest by a wide margin. CPU only. Digital PDFs only — no text layer means no output. |
| pdfplumber | ~18 pages/s | Character-level boxes, best-in-class table extraction, MIT. |
| Docling (hybrid) | ~0.463 s/page | ~30x slower than PyMuPDF. Strong structured output for RAG. |
| Marker | ~53.9 s/page | ~100x slower than Docling. **Wants the GPU.** |

Sources:
- https://docs.bswen.com/blog/2026-06-04-benchmark-comparison/
- https://pdfmux.com/blog/pymupdf-vs-pdfplumber/
- https://pdfmux.com/blog/pdfmux-vs-pymupdf-vs-marker-vs-docling/

**Decisive constraint:** Marker and Docling's ML paths contend for the same
16 GB of VRAM that Ollama needs. On a single-GPU box, an ML document converter
and a local LLM are competing for the scarce resource. That rules them out here
regardless of accuracy.

**Selection:** PyMuPDF as the primary extractor, pdfplumber as a fallback for
pages where table structure matters. Scanned PDFs with no text layer produce
near-zero characters and are recorded as a distinct coverage state rather than
silently counted as empty.

## Document Parsing Threat Model

### CORRECTION (2026-08-04): research the shipped parser, not pypdf

An earlier draft of this file assessed **pypdf** and concluded "DoS, not RCE."
That was wrong for this project: the selected PDF parser is **PyMuPDF/MuPDF**, not
pypdf. Re-run against the actual dependency, the threat is RCE-class, not merely
liveness. The pypdf CVE list has been removed; it does not apply.

### PDF — PyMuPDF/MuPDF, RCE-class

| CVE | Mechanism |
|-----|-----------|
| CVE-2026-3308 | MuPDF integer overflow in `pdf_load_image_imp` → OOB heap write → possible RCE (CVSS 7.8), fixed in MuPDF 1.28.0 |
| CVE-2026-3029 | PyMuPDF path traversal / arbitrary file write via the embedded-file **CLI** extraction path (fixed 1.26.7) — ordinary imported text parsing need not exercise it |

Sources: https://mupdf.com/releases/cve ·
https://www.cve.org/CVERecord?id=CVE-2026-3029 ·
https://securityonline.info/mupdf-integer-overflow-vulnerability-cve-2026-3308-rce/ ·
https://github.com/pymupdf/PyMuPDF/releases

Implication: parsing hostile PDFs carries **code-execution** risk. The parser
sandbox (bubblewrap) is therefore a **containment** control, not merely a
liveness one. Liveness (hang/OOM) is still handled — wall-clock + rlimits — but it
is the smaller concern now. **Version pin:** an approved PyMuPDF bundling MuPDF ≥
1.28.0; preflight asserts both `pymupdf_version` and the embedded `mupdf_version`
(package version alone is insufficient — a fixed PyMuPDF can still bundle an
affected MuPDF).

### Office formats — XXE, and this one exfiltrates

DOCX/XLSX/PPTX are ZIP archives of XML. A crafted document can carry XML external
entity declarations (including inside OMML math tags) that read local files or
trigger SSRF. This is data leaving the box, not a hang.

- Python's `xml.etree.ElementTree` is documented as not secure against
  maliciously constructed data.
- openpyxl resolved external entities by default (CVE-2017-5992).

Sources: https://www.yeswehack.com/learn-bug-bounty/xml-external-entity-guide-xxe,
https://security.snyk.io/vuln/SNYK-PYTHON-OPENPYXL-40459,
https://github.com/microsoft/markitdown/issues/1565

Controls: `defusedxml`, or lxml with `resolve_entities=False, no_network=True`;
plus member-count / per-member size / aggregate size / compression-ratio / path
gates against zip bombs. Enforce with a guardrail test in the shape of
`shared/tests/test_sherlock_purity.py`.

### Legacy `.doc`/`.xls` — catdoc is not safe

`catdoc`/`xls2csv` carry a heap-corruption / code-execution CVE
(TALOS-2024-2132) and predictable-temp symlink issues. **Not used.** C7A uses exact
Debian Antiword `0.37-17`, authenticated by parser success inside C3. Revision 17
removes a document-summary parser with buffer overreads; the package has no active
upstream. Measured output also showed that its UTF-8 mapping emits CESU-8 surrogate
pairs for non-BMP characters, so C7A narrowly repairs paired surrogates and rejects
every malformed form.

The earlier `.xls` shortlist was corrected by accepted erratum E12. `xlrd 2.0.2` is pure Python and
technically narrow, but its exact licence retains the original BSD advertising clause;
GNU classifies that licence as GPL-incompatible. LibreOffice is compatible but carries
a far larger native conversion surface. C7B uses the
MIT-licensed `python-calamine==0.8.2`: its attested 2.25 MiB wheel has a narrow runtime
closure and matched `xlrd` on the public XLS fixture. The exact CPython 3.14 / Linux
x86-64 artifact is hash- and ABI-pinned; other platforms fail closed. It exposes cached
formula values, not formula text or recalculation. `olefile` was unnecessary: parser
success independently authenticates XLS after CFB candidate routing. Every accepted
parser runs inside bubblewrap.

Sources: https://www.talosintelligence.com/vulnerability_reports/TALOS-2024-2132 ·
https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=968812 ·
https://manpages.debian.org/testing/antiword/antiword.1.en.html ·
https://github.com/python-excel/xlrd/blob/2.0.2/LICENSE ·
https://www.gnu.org/licenses/license-list.html#OriginalBSD ·
https://pypi.org/project/python-calamine/0.8.2/

### Sandbox mechanism (verified on this box)

`bwrap` (bubblewrap 0.11.1) is installed; a minimal `--unshare-net --unshare-pid
--die-with-parent --cap-drop ALL` invocation runs. firejail/nsjail absent. This is
the mandated mechanism. Fallback: preflight fails if bubblewrap is unavailable.
Reference: https://github.com/containers/bubblewrap

## Prompt Injection

OWASP ranks prompt injection #1 in the 2025 Top 10 for LLM Applications.
Applicable guidance:

- Treat all retrieved content as untrusted.
- Separate and clearly delimit untrusted content from instructions.
- Treat model output as untrusted data; sanitize as you would any external input.
- Constrain the model's role and enforce strict adherence to a narrow task.
- Defense in depth: least-privilege tooling, human approval for high-risk actions.

The honest position in that literature is that no prevention method is foolproof.
That is the argument for giving the model zero authority: no tools, no actions,
and no output path except a validated schema rendered as text.

Sources:
- https://owasp.org/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf
- https://www.oligo.security/academy/owasp-top-10-llm-updated-2025-examples-and-mitigation-strategies

## Design Consequences

1. **Two tracks, one aggregator.** Deterministic regex + checksum validators
   (Luhn, IBAN mod-97) produce identifier counts. The model reads chunks and
   answers a fixed worksheet for classification and unstructured findings.
   Neither substitutes for the other — regex cannot find "attached is the layoff
   list," and the model must not be trusted to count SSNs.
2. **Mandatory evidence.** Every model finding carries a quoted span and offset.
   Uncited claims are dropped by the aggregator. This makes findings auditable
   and gives injected instructions nothing to hold onto.
3. **"Insufficient evidence" is a schema value.** Hedging appears when
   uncertainty has no structured home. Give it a column and it leaves the prose.
4. **Validate, retry once, then record.** Schema-invalid responses become a
   coverage state (`model_invalid`), not a silent gap.
5. **Checkpointed and resumable.** Runtime is measured in hours. The job must
   survive a crash, a cancel, and a reboot without redoing completed files.
6. **Two concurrency domains.** Text extraction is CPU-bound and embarrassingly
   parallel across 24 cores. Model inference is serial on one GPU. They are
   separate stages with separate worker models, not one pipeline.
7. **Two-phase analysis.** At ~73,300 enumerated candidate documents, running the model over
   everything is the difference between hours and days. Phase 1 runs
   deterministic detectors over 100% of extracted text — this is what establishes
   detector coverage, and it is fast and parallel. Phase 2 runs the model
   worksheet only on files Phase 1 flagged or could not classify. Detector
   coverage can be complete while model-review coverage remains explicitly
   partial and separately reported; model cost drops by roughly an order of
   magnitude.
8. **Trust magic bytes, not extensions.** In 116,138 files harvested from open
   directories, mislabeled extensions are certain. Sniff the header before
   choosing a parser, both for correctness and to stop a zip bomb named `.pdf`
   from reaching a PDF parser.
