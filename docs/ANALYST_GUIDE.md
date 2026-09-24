# Analyst User Guide

> **Experimental feature.** Analyst's inventory, parser sandbox, durable state,
> Phase 1/Phase 2 worker orchestration, local Ollama boundary, atomic report
> publication, standalone-directory and extraction-manifest launchers, opt-in
> post-extract handoff, task hydration and report browser are implemented and tested.
> Public-only Ollama and full production-pipeline acceptance passed using synthetic data
> only; private real-document validation remains explicitly deferred.

Analyst reviews directories of already-extracted documents and builds a per-host
exposure report. It looks for identifiers such as Social Security numbers, payment-card
numbers, bank details, email addresses and phone numbers, then uses a local Ollama model
to classify selected documents and suggest findings with source quotes.

It is an assistant, not an authority. A finding means "review this," not "take action."
Analyst never logs in to a server, downloads a document, copies an original file or
changes a scan result.

## What happens during a run

1. Analyst inventories the selected directory without following symlinks or crossing
   into nested mounts.
2. Each supported document is opened by type, based on its contents rather than its
   filename extension.
3. Document parsers run one file at a time inside a restricted Linux sandbox.
4. Deterministic detectors scan all successfully extracted text.
5. In **Fast** mode, the model reviews selected files. In **Deep** mode, it reviews all
   supported files with usable text.
6. Model findings are kept only when their quoted evidence exists in the extracted
   source text.
7. Analyst writes a coverage-first report. Files that could not be parsed stay visible
   as failures or unsupported content; they are not quietly counted as clean.

The desktop report browser keeps deterministic hits separate from model suggestions.
Accept/Reject records an explicit human review decision. Export starts with nothing
selected; choose individual model rows or explicitly select all before writing a 0600
JSONL or spreadsheet-guarded CSV copy.

Only one Analyst worker can own the GPU at a time. Other software can still use the GPU,
so a run may slow down when the machine is busy. Closing or hiding the desktop window
does not stop the worker.

## Dependency setup

The interactive `bash install.sh` workflow offers Analyst as an optional, default-No
step. Declining it leaves core Dirracuda fully usable. The step installs the reviewed
system tools, uses the controlled dependency installer below, verifies its exact pins,
and runs a public strict-sandbox preflight. It does not start an analysis run.

For manual setup, use these commands from the repository root:

V1's reviewed dependency lane is Linux x86-64, CPython 3.14. Other platforms fail
preflight until their native artifacts receive separate review.

```bash
./venv/bin/python scripts/install_analyst_deps.py
./venv/bin/python scripts/install_analyst_deps.py --check
```

The installer downloads hash-pinned sources and wheels, then builds PyMuPDF against the
reviewed MuPDF source. That build can take a while. Do not replace the installer with a
plain `pip install -r` command; the requirements file intentionally blocks that route.

Strict parser isolation also needs `bwrap`, `prlimit`, a working systemd user manager
with cgroup v2, and Antiword 0.37 at Debian package revision `0.37-17`:

```bash
command -v bwrap prlimit systemd-run antiword
dpkg-query -W -f='${Version}\n' antiword
```

Analyst fails preflight if the complete sandbox chain does not work. Finding the four
binaries is useful diagnosis, but it is not proof that the sandbox works.

Install and start Ollama using the [official Ollama Linux guide](https://docs.ollama.com/linux).
Bind its API to `127.0.0.1:11434`, set `OLLAMA_NO_CLOUD=1` and pin the reviewed server
version rather than a mutable container tag. Basic checks:

```bash
curl --fail --noproxy '*' http://127.0.0.1:11434/api/version
curl --fail --noproxy '*' http://127.0.0.1:11434/api/tags
```

If Ollama runs only in a container, its CLI may not exist on the host; use the API checks
above and run `ollama list` inside that container. Do not expose port 11434 on a LAN or
VPN interface: the local API has no authentication. A future supported LAN/Tailscale
mode is planned around an authenticated TLS gateway while Ollama stays on loopback; it
will not make the raw API public.

The approved model is `qwen3.6:27b` at digest
`a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`. A matching
tag with a different digest is rejected. Model tags can move, so do not bypass this
check or substitute a similarly named model.

### Why llama.cpp models are not fingerprinted

Ollama gives every model a SHA-256 digest, so Analyst can prove which exact model
file answered. llama.cpp has no equivalent: it reports a name, a file path and some
properties, and nothing more. You have the server's word for it.

So a run records one of two identity kinds, and always says which:

- **digest** - verified. An Ollama tag plus its SHA-256.
- **reported** - the server's own account of itself, not verified.

A reported identity is never displayed as verified anywhere. It is not a weaker
version of a digest; it is a different claim, and the report says so.

## What remote support is for

Remote backends are built for **a model server you own, on a network you control** --
a box under your desk, a machine in your rack, a host on your own Tailscale network.
That is the case it was designed, tested and reasoned about.

It is **not** built for reaching a model server run by somebody else, or one exposed to
the open internet. Two things follow, and both are deliberate:

- **A public plaintext endpoint is refused outright**, with no override. Analyst will not
  send harvested file text to an arbitrary address in the clear, whatever you tick.
- **Authenticated remote runs are not supported unattended.** A run executes in a
  detached worker process that outlives the app, and that process cannot decrypt a
  Keymaster-held bearer token. It fails with "Keymaster is locked" rather than quietly
  connecting without the token. Getting a secret into a detached process safely is a
  design problem in its own right, and it was left alone rather than solved badly.

So the supported shapes today are:

| Setup | Supported |
| --- | --- |
| Ollama or llama.cpp on this machine | Yes, and it needs no acknowledgement |
| A server on your private LAN, Tailscale, or IPv6 ULA, no token | Yes, with an explicit per-profile acknowledgement |
| An https server with a valid or pinned certificate, no token | Yes |
| Anything needing a bearer token | Not unattended -- see above |
| A public plaintext address | Refused, no override |

If you need the token path, say so and it can be built. Until then, treat remote support
as "my hardware, my network".

## Serving Analyst from a shared box

Analyst can send work to a model server on another machine. That is the point of the
remote profiles: offload the analysis to hardware that can actually run a large model.

**Analyst does not arbitrate access to a server it does not own.** If two people point
Analyst at the same box, they will share it. Analyst warns when the server looks busy,
reports "server at capacity" clearly when it is, and records contention with the run --
but it will not queue, lock or wait for you. Neither Ollama nor llama.cpp offers a lock
to build that on, and a real one would need a coordinator service this tool does not
have. Analyst assumes you administer the hardware.

### Sizing the context

llama.cpp fixes its context when the server starts, not per request. Analyst reads the
limit and refuses a run that cannot fit, naming both numbers:

> Model qwen3.8-27b on mimir accepts 32768 tokens. This run needs 42058.

Raise `--ctx-size` for that preset, or pick a model with more room. A host read needs
more context than per-chunk generation does, so size for the larger of the two.

If the server runs in router mode it reports a context of `0` at `/props`, because the
router itself holds no model. Analyst reads the real number from the model entry
instead. A context it cannot determine is treated as unknown, never as unlimited -- the
run proceeds and the server remains the backstop, because llama.cpp refuses an oversized
prompt cleanly rather than silently truncating it.

### The Ollama equivalents

`OLLAMA_NUM_PARALLEL` sets how many requests a model serves at once.
`OLLAMA_MAX_LOADED_MODELS` sets how many models stay resident. Two clients asking for
different models will make Ollama load and evict repeatedly; that is server RAM and
configuration, and a client cannot fix it.

### A cold model is not a stuck one

A router may hold a model unloaded or asleep. The first request loads it, which can take
minutes for a large model. Analyst shows "loading the model" rather than a stall, and its
read deadline is set to tolerate a cold load. Give it time before cancelling.

## Running Analyst

The primary entry point is:

1. Start Dirracuda with `./dirracuda`.
2. Open **Accessories → Analyst**.
3. Choose an existing directory, or select an exact persisted extraction manifest.
   A manifest run analyzes only the final saved files named by that extraction.
4. Choose an output base if desired and enter a report label. Manifest runs carry the
   persisted protocol/host identity; standalone directories do not guess one.
5. Choose Fast or Deep mode and select **Analyze**.

The launcher supports both standalone directories and recent primary-database
extraction manifests. A manifest choice is bound to its exact summary row id; it never
silently switches to a newer extraction for the same host.

**Fast** is the sensible default. Detectors still scan every document with extracted
text; the model spends time only on selected material. **Deep** costs much more time and
GPU capacity because every supported document with text goes through model review.

The automatic post-extract offer is opt-in and off by default. Enable it in the Analyst
tab. The offer appears only after the extraction summary has been durably persisted and
uses that exact manifest rather than reconstructing identity from filenames. Accepted
offers always use Fast mode and strict isolation.

### Common use cases

- **Review files already on disk:** choose the directory, supply the correct host label
  and write the report beside the source under `_analyst/`.
- **Continue interrupted work:** reopen Running Tasks and resume the existing run. Do
  not start a second run against the same directory to work around an interruption.

### Pause, cancel and resume

- **Hide** closes the progress view but leaves the run active.
- A normal **Cancel** checkpoints the run. Finished files stay finished; unfinished
  files remain available for resume.
- **Resume** continues compatible unfinished work. If the source, parser versions,
  model digest, prompt or relevant settings changed, Analyst refuses to silently mix
  the old and new run.
- **Abandon** is final. Remaining files are marked abandoned so the report can describe
  the incomplete coverage honestly.

A crash or reboot leaves resumable work, not a report labelled complete. The Running
Tasks view recovers active and interrupted runs from Analyst's sidecar database after
the GUI restarts.

## Reading the report

Coverage comes first. Check it before reading the findings.

- **Detector-scanned** means deterministic identifier checks ran on extracted text.
- **Model-reviewed** means the approved model returned a valid structured answer.
- **Detector-only** is a completed Fast-mode outcome, not a model review.
- **No text layer** usually means an image-only or scanned PDF. V1 does not include OCR.
- **Unsupported**, **parse failed**, **timeout** and similar rows are coverage gaps, not
  clean documents.

It is normal for a Fast report to say "100% detector-scanned" and a much smaller
percentage "model-reviewed." Those percentages describe different stages.

Model suggestions are marked for human review and include a verified source quote.
Quote grounding prevents invented evidence from entering the finding list, but it does
not prove that the category or interpretation is correct. The benchmark intentionally
accepts a small false-positive review cost because the operator remains the decision
maker.

The release target produces:

- a static HTML report with no JavaScript or remote assets;
- canonical JSONL evidence;
- a CSV findings view guarded against spreadsheet-formula injection; and
- CSV or JSONL exports of findings selected during human review.

Exports contain report rows, not copies of the source documents. Default output is under
the source directory's `_analyst/` tree, and that tree is excluded from later Analyst
runs.

## Supported content and known gaps

| Content | V1 behavior |
|---------|-------------|
| Plain text and RTF | Text extraction with bounded encoding and RTF handling |
| PDF | Text-layer extraction; no OCR |
| DOCX, XLSX and PPTX | Non-macro OOXML packages only |
| Legacy `.doc` | Antiword in its own sandbox |
| Legacy `.xls` | python-calamine in its own sandbox |
| Images and scanned PDFs | Reported as having no usable text |
| Macro-enabled or Strict OOXML | Unsupported |
| Legacy `.ppt` and other binary formats | Unsupported |

Spreadsheets are read as stored cell values. Analyst does not run formulas, macros or
external links. Cached formula results can be missing or stale, and formatting is not a
substitute for data semantics.

Large, deeply nested or highly compressed files can hit safety limits. A limit failure
is recorded against that file while the rest of the run continues when safe.

## Troubleshooting

### There is no Analyst tab

Use the current `feature/ollama-analyst` branch and start the desktop through
`./dirracuda`. The tab is under **Accessories → Analyst**. Installing parser dependencies
does not add the tab to older builds.

### Dependency check fails

Run:

```bash
./venv/bin/python scripts/install_analyst_deps.py --check
```

The check is exact. A newer package is not automatically accepted, and the legacy Excel
wheel currently requires CPython 3.14 on Linux x86-64.

### Ollama is unreachable

Check the local service and installed models:

```bash
ollama --version
ollama list
```

Analyst accepts only a literal loopback Ollama endpoint and does not follow redirects or
use proxy environment variables. Review Ollama's
[official troubleshooting guide](https://docs.ollama.com/troubleshooting) if the local
service is not responding.

### The model is listed but rejected

The tag and digest must both match the benchmarked model. Refreshing or re-pulling a tag
can change its digest. Treat the rejection as a compatibility stop, not a prompt to
disable verification.

### Sandbox preflight fails

Confirm the system tools and Antiword revision shown in the dependency section. Strict
mode also needs a functioning user systemd manager and cgroup v2 task controls. Analyst
tests the real launch chain and fails closed if containment cannot be established.

A reduced-isolation mode is planned for deliberate one-run use. It runs parsers with
less protection under the desktop user's account, is recorded in the report and is not
available to the automatic post-extract hook. Strict mode is the normal choice.

### A run is slow

Large language models are slow, and Analyst does not reserve the GPU against unrelated
programs. Use Fast mode, let other GPU work finish, or leave the run in the background.
Only one Analyst run uses the GPU at once. Explicit Ollama resource refusals use bounded
backoff and do not spend one of the two model-answer attempts. After repeated refusals,
the run pauses for five minutes and requires an explicit Resume; ambiguous disconnects
remain conservatively charged because Analyst cannot prove whether inference started.

### A PDF says `no text layer`

The PDF is probably a scan or contains only images. V1 does not perform OCR. Review the
original manually or process it with an approved OCR workflow outside Analyst.

### A file is marked changed or unsupported

Analyst fingerprints files during inventory and checks them again before parsing. A
change produces `source_changed_since_inventory` instead of analyzing different bytes
under the old identity. Unsupported content either failed type authentication or has no
approved V1 parser.

### The database is busy after a crash

Do not delete `analyst.db` or an adjacent SQLite journal by hand. Another GUI or worker
may still own a short transaction, and the journal may be required for crash recovery.
Close duplicate Dirracuda windows, verify the worker state through Running Tasks once
that UI ships, then retry. The sidecar lives at
`~/.dirracuda/data/experimental/analyst.db`.

## Privacy and safety

Analyst connects only to a literal-loopback Ollama endpoint, disables redirects, ignores
ambient proxies, rejects known cloud tag forms and requires the approved local model
digest. It cannot prove that the Ollama server itself has no external network access.
For a stronger local-only setup, disable Ollama cloud features and enforce egress policy
at the operating-system or container boundary.

Raw identifiers, source quotes and model findings are sensitive. Analyst stores them in
its owner-only sidecar and reports. File permissions reduce accidental local exposure;
they do not protect against another process running as the same user, privileged access,
filesystem snapshots or backups. SQLite journals and backups may retain deleted values.

Parser sandboxes block network access and expose only the current source file plus a
private temporary directory. This reduces parser risk, but it is not protection against
an exploitable host-kernel bug.

Treat every report like the documents it summarizes. Keep it out of source control,
shared chat, tickets and ordinary logs unless that destination is approved for the raw
data.

## More detail

- [Analyst implementation contract](dev/ollama_integration/CONTRACT.md)
- [Accepted contract corrections](dev/ollama_integration/CONTRACT_ERRATA.md)
- [Benchmark and model decision](dev/ollama_integration/BENCHMARK.md)
- [PyMuPDF and MuPDF notice](../licenses/PyMuPDF-MuPDF-NOTICE.md)
- [defusedxml notice](../licenses/defusedxml-NOTICE.md)
- [Antiword notice](../licenses/antiword-NOTICE.md)
- [python-calamine notice](../licenses/python-calamine-NOTICE.md)
