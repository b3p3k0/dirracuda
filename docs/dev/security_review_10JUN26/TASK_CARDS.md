# Security Review Task Cards

Execution model: one card at a time

Planning model: Claude plan-only session before every DA session

Commit model: no commit unless HI says exactly `commit`

## Global Rules

Every card must:

1. Confirm branch, commit, and worktree before edits.
2. Read the approved card plan.
3. Reproduce or statically confirm the issue.
4. State root cause.
5. Check line counts before and after.
6. Implement only the named card.
7. Add focused regression tests.
8. Run exact declared validation.
9. Review README and Technical Reference for drift.
10. Report using `SOP_CONSTRAINTS.md`.

No card may modify requirements, database schema/migrations, auth, CI, or the
GUI-to-CLI subprocess boundary.

## Sequence

```text
C0
 -> C1 -> C2 -> C3
 -> C4 -> C5 -> C6 -> C7 -> C8
 -> E0 -> E01 ... E12
 -> C9
```

C1-C3 are ordered because endpoint pinning and TLS policy consume the transport
created in C1. C4-C8 may proceed only after C3 unless RA records a reason to
preserve the sequence while waiting on a C2 architecture blocker.

## C0 - RA Baseline Refresh

Type: RA evidence card, no product code

### Objective

Confirm execution starts from known repo truth and record any drift from the PA
baseline.

### Tasks

- Record branch, commit, status, Python version, and installed optional deps.
- Recount pass-only product exception handlers using the AST method in
  `EXCEPTION_AUDIT_PLAN.md`.
- Record touched-file candidate line counts.
- Run the 95-test targeted baseline.
- Record whether `xvfb-run` is available.
- Do not fix failures.

### Acceptance

- `EXECUTION_TRACKER.md` contains the refreshed baseline.
- Differences from `4320614` are explained.
- Any pre-existing failure is recorded with exact command and error summary.

## C1 - Shared Redirect-Safe HTTP Transport

### Issue

Target HTTP operations use redirect-following `urllib` behavior and may inherit
ambient proxy configuration.

### Root cause

Verifier, browser, probe/listing, and extraction paths own transport behavior
independently.

### Scope

- Add one standard-library-only shared HTTP transport.
- Disable proxy inheritance by construction.
- Implement same-origin redirects with a maximum of three.
- Add stable `redirect_blocked` and `redirect_limit` outcomes.
- Migrate verifier, browser read/download, probe/listing, and bulk extraction.
- Preserve current body and streaming size limits.

### Acceptance

- All four originally identified direct fetch paths use the shared transport.
- No target path calls bare `urllib.request.urlopen`.
- Cross-scheme, cross-host, cross-port, credential-bearing, malformed, and
  fourth-hop redirects are rejected.
- Relative and same-origin redirects up to three hops work.
- HTTP proxy environment variables do not affect the destination.
- Existing non-redirect HTTP tests pass.

### Validation

```bash
./venv/bin/python -m pytest shared/tests/test_http_transport.py -q
./venv/bin/python -m pytest \
  shared/tests/test_http_operation.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_protocol_extract_runner.py -q
rg -n "urllib\\.request\\.urlopen" \
  commands/http shared/http_browser.py gui/utils/http_probe_runner.py \
  gui/utils/protocol_extract_runner.py
```

### HI test

No live-network test. Optional local fixture smoke only.

## C2 - Recorded-IP Endpoint Pinning

### Issue

Some browse/probe/extract flows reconnect through a saved hostname, permitting
DNS drift or rebinding away from the discovered IP.

### Root cause

Connection destination and HTTP/TLS virtual-host identity are represented by
one hostname in high-level request APIs.

### Scope

- Extend the C1 transport to separate socket destination from request identity.
- Connect to recorded IP whenever present.
- Use saved hostname only for Host, SNI, and strict certificate identity.
- Remove hostname fallback after IP connection failure.
- Support IPv4 and IPv6 literals.
- Migrate all HTTP target consumers.

### Stop gate

If the approved standard-library design cannot provide pinned IP plus correct
SNI/certificate verification without unsafe global monkeypatching, stop before
code changes and return to HI/RA. Do not add a dependency or weaken strict TLS.

### Acceptance

- Fake DNS resolution cannot change the socket destination when a recorded IP
  is supplied.
- Virtual-host Host and SNI remain the saved hostname.
- Strict TLS validates against saved hostname.
- IP-only endpoints continue to work.
- Connection failure does not retry via DNS hostname.

### Validation

```bash
./venv/bin/python -m pytest shared/tests/test_http_transport.py -q
./venv/bin/python -m pytest \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_protocol_extract_runner.py \
  gui/tests/test_server_list_http_endpoint.py -q
```

### HI test

Use a local virtual-host fixture only if the automated SNI test cannot fully
exercise the platform SSL stack.

## C3 - Canonical HTTP TLS Policy

### Issue

HTTP TLS behavior has multiple persisted GUI keys, direct config reads, and
hardcoded consumer values.

### Root cause

Each UI and runtime path evolved its own policy lookup.

### Scope

- Make `http.verification.allow_insecure_tls` the sole persisted default.
- Add the setting to App Config through existing config abstractions.
- Add one shared resolver backed by `SMBSeekConfig`.
- Make scan-dialog choices transient per-run overrides.
- Stop persisting independent unified/legacy scan-dialog TLS policy.
- Use fixed migration precedence: explicit canonical core value, unified scan
  key, legacy HTTP scan key, then `true`.
- Persist a migrated value through the config-store abstraction.
- Stop reading and writing the retired GUI TLS keys after migration; stale keys
  may remain on disk.
- Migrate browser, probe, classifier, and extract consumers.
- Remove direct JSON reads for this setting.
- Keep default `true`.
- Add plain-language MITM warnings to runtime docs in C9.

### Acceptance

- One persisted default remains.
- App Config changes affect later browser/probe/extract operations.
- A scan override affects that run only and does not silently rewrite the
  application default.
- Legacy keys cannot override an existing canonical value.
- No target consumer hardcodes `allow_insecure_tls=True`.
- No target consumer reads config JSON directly for the value.

### Validation

```bash
./venv/bin/python -m pytest \
  shared/tests/test_config_validation_paths.py \
  gui/tests/test_app_config_dialog.py \
  gui/tests/test_unified_scan_dialog.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_protocol_extract_runner.py \
  gui/tests/test_action_routing.py \
  gui/tests/test_dashboard_bulk_ops.py -q
rg -n "allow_insecure_tls=True|allow_insecure_tls = True" \
  gui shared commands experimental
```

### HI test

Toggle the App Config default, reopen an HTTP browser and probe, and confirm the
displayed/observed policy changes. Launch one scan with the opposite transient
choice and confirm the App Config default remains unchanged afterward.

## C4 - SMB Extract Path Containment

### Issue

SMB bulk extract uses a server-supplied share as a local path component.

### Scope

- Keep exact remote share for SMB calls and reporting.
- Create deterministic safe local share labels.
- Resolve collisions between distinct hostile shares.
- Sanitize relative file parts.
- Verify final resolved destination remains under resolved download root before
  any parent creation or file open.
- Record containment rejection without attempting a download.

### Acceptance

- `..`, slash, backslash, absolute, empty, dot-only, and collision cases remain
  beneath the root.
- Symlinked-parent escape is rejected.
- Remote `getFile` still receives the original share.
- Normal share layout is unchanged.

### Validation

```bash
./venv/bin/python -m pytest \
  gui/tests/test_extract_runner_clamav.py \
  shared/tests/test_quarantine_postprocess.py -q
```

### HI test

No live SMB target required.

## C5 - Pre-Decode Image Pixel Guard

### Issue

The application pixel limit is checked after full image decode.

### Scope

- Inspect dimensions after lazy open and before `load()`.
- Validate malformed and over-limit dimensions.
- Preserve Pillow decompression-bomb handling.
- Add direct unit tests that prove `load()` is not called after rejection.

### Acceptance

- Over-limit image raises before decode.
- At-limit image decodes.
- Corrupt image behavior remains user-readable.

### Validation

```bash
./venv/bin/python -m pytest \
  gui/tests/test_image_viewer_window.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_ftp_browser_window.py -q
```

### HI test

Open one normal image in the browser viewer.

## C6 - Bounded ZIP Import

### Issue

ZIP import extracts an unbounded archive tree.

### Scope

- Remove `extractall`.
- Enforce 32-member, 256-MiB-total, and 128-MiB-selected limits.
- Consider one root-level regular JSON/CSV payload only.
- Prefer JSON, then lexical CSV.
- Reject duplicate eligible names and encrypted selected members.
- Stream to a generated fixed temporary filename with an actual-byte cap.
- Use existing JSON/CSV readers after streaming.
- Apply identical validation to import, preview, and format-check paths.

### Acceptance

- No archive member name becomes a destination path.
- Nested-only, too-many-member, total-size, selected-size, streamed-overrun,
  encrypted, duplicate-name, and no-payload archives fail deterministically.
- Valid exported ZIPs continue to import and preview.

### Validation

```bash
./venv/bin/python -m pytest gui/tests/test_data_import_engine.py -q
rg -n "extractall" gui/utils/data_import_engine.py
```

### HI test

Import one normal Dirracuda ZIP export in validate-only mode.

## C7 - FTP Remote-Path Control Validation

### Issue

Supported CPython blocks CR/LF, but Dirracuda does not state its own remote-path
control-character contract.

### Scope

- Reject C0 controls and DEL before `SIZE` and `RETR`.
- Share validation between read and download paths.
- Do not echo raw controls in errors or logs.
- Keep all other path validation unchanged.

### Acceptance

- Control-bearing paths fail before any FTP command method is called.
- Ordinary and Unicode paths continue to work.

### Validation

```bash
./venv/bin/python -m pytest gui/tests/test_ftp_browser.py -q
```

### HI test

No.

## C8 - SMB POSIX Basename Compatibility

### Issue

`preserve_structure=False` uses POSIX basename rules on a Windows-form SMB path.

### Scope

- Use `PureWindowsPath` or equivalent standard-library Windows semantics.
- Reject empty/root-only basenames.
- Preserve structured-download behavior.

### Acceptance

- `\\folder\\file.txt` saves as `file.txt` on POSIX.
- Structured downloads remain unchanged.

### Validation

```bash
./venv/bin/python -m pytest shared/tests/test_smb_browser.py -q
```

### HI test

No.

## E0 - Exception Ledger Freeze

Type: documentation and analysis; no product-code changes

### Scope

- Re-run the AST inventory.
- Map every X001-X448 entry to current code context.
- Create `EXCEPTION_AUDIT_LEDGER.md`.
- Add operation, initial classification proposal, rationale, test owner, and
  current line for every entry.
- Record additions/removals caused by C1-C8 separately; do not silently alter
  the baseline population.

### Acceptance

- All 448 IDs appear exactly once.
- Every item remains assigned to E01-E12.
- No product code changed.

## E01-E12 - Exception Audit And Remediation Batches

Each card owns the IDs assigned in `EXCEPTION_AUDIT_PLAN.md`.

### Required process

1. Inspect the full try/except operation and caller contract.
2. Classify each item.
3. Record rationale in the ledger.
4. Remediate `should-log-debug` and `should-surface`.
5. Add or update focused tests for behavior changes.
6. Keep intentional lifecycle/polling silence silent.
7. Run focused tests for every touched subsystem.
8. Confirm no raw secret or untrusted content enters logs.

### Acceptance per batch

- Every assigned ID has final classification and evidence.
- No assigned non-intentional item remains pass-only.
- No unassigned handler is changed unless RA first moves it into the card.
- Batch size remains at or below 40 baseline items.
- The AST baseline comparison and focused tests pass.

### Batch order

| Card | IDs | Emphasis |
|---|---|---|
| E01 | X001-X040 | command/backend/extract/probe I/O |
| E02 | X041-X080 | shared protocol, config, path, cleanup |
| E03 | X081-X120 | experimental services, Web UI service control, tools |
| E04 | X121-X160 | FTP/HTTP browser lifecycle and operations |
| E05 | X161-X200 | SMB browser and dashboard batch/scan operations |
| E06 | X201-X240 | scan and server-list batch operations |
| E07 | X241-X280 | server-list details/table/window and App Config |
| E08 | X281-X320 | extract/scan dialogs and Censys browser |
| E09 | X321-X360 | experimental GUI, database dialogs, viewers |
| E10 | X361-X400 | help, image, keymaster, Reddit, task dialogs |
| E11 | X401-X440 | dork windows, dashboard widget, shared GUI helpers |
| E12 | X441-X448 | messagebox/style/dispatcher tail |

Validation commands are finalized in each approved plan because touched test
files depend on classification. Every batch also runs:

```bash
./venv/bin/python scripts/run_agent_testing_workflow.py --lane quick
```

## C9 - Documentation, Regression, And Closeout

### Scope

- Update root README and Technical Reference to implemented truth.
- Update this workspace's reconciliation, risk, tracker, and lessons.
- Record final exception classification totals.
- Run all final gates in `VALIDATION_PLAN.md`.
- Record deferred Python 3.8 risk and review trigger.

### Acceptance

- No planning statement is presented as implemented unless validated.
- All automated gates pass or failures are explicitly accepted by HI.
- All required manual checks pass.
- Every task card has evidence and final status.
