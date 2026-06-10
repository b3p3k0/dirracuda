# Findings Reconciliation

Date: 2026-06-10

Baseline: `development` at `4320614`

## Purpose

This document reconciles three inputs:

1. The original report in `INITIAL_REPORT.md`.
2. Codex's independent inspection of current repo truth.
3. The assessing agent's response to Codex's counters.

The dispositions below are final for planning. Claude may identify blockers but
must not reopen product decisions without HI/RA approval.

## Disposition Matrix

| ID | Finding | Verification | Final disposition | Card |
|---|---|---|---|---|
| F1A | HTTP redirects can escape the recorded endpoint | Confirmed and under-scoped in the original report | Fix in current wave | C1 |
| F1B | Saved hostname can become the connection destination | Confirmed during reconciliation | Fix separately in current wave | C2 |
| F2 | SMB bulk extract trusts server-supplied share in local path | Confirmed | Fix with segment sanitization and resolved-root containment | C4 |
| F3 | Image pixel limit runs after full decode | Confirmed | Move project limit before decode | C5 |
| F4 | ZIP import has no decompression/resource bounds | Confirmed | Remove `extractall` and stream one bounded payload | C6 |
| F5 | 448 pass-only exception handlers are unaudited | Count confirmed; original blanket-logging remedy rejected | Audit and remediate in bounded current-wave batches | E0-E12 |
| F6 | FTP CRLF command injection | Not exploitable on supported CPython | Defense-in-depth validation only | C7 |
| F7 | SMB basename uses POSIX semantics on Windows paths | Confirmed latent defect | Fix with Windows path semantics | C8 |
| F8 | Insecure TLS behavior is inconsistent and incompletely documented | Confirmed as policy drift rather than a wholly silent default | Establish one persisted default plus per-run override | C3 |
| N1 | Python 3.8 remains documented as supported after EOL | Confirmed maintenance/security risk | Defer with explicit risk entry | R-12 |

## F1A - Redirect SSRF

### Evidence

Default `urllib.request.urlopen()` follows redirects at:

- `commands/http/verifier.py`
- `shared/http_browser.py` read path
- `shared/http_browser.py` download path
- `gui/utils/protocol_extract_runner.py` download path

The bulk-extract download path was missing from the original report. Listing
requests in probe/extract also transit through the verifier and therefore share
the behavior.

### Final contract

- Use one shared transport implementation.
- Disable environment proxy inheritance.
- Permit at most three redirects.
- A redirect is valid only when normalized scheme, host, and effective port are
  identical to the previous URL.
- Reject scheme-relative URLs that change authority.
- Reject malformed, credential-bearing, or unsupported-scheme locations.
- Surface deterministic `redirect_blocked` and `redirect_limit` outcomes.

TLS verification being disabled is not required for this SSRF. It increases
MITM risk but is a separate policy issue.

## F1B - Endpoint Pinning

### Evidence

Bulk HTTP extraction retries via `active_request_host` after an IP connection
failure and then reuses that hostname for later listing and download requests.
HTTP browsing has a similar IP-or-hostname connection pattern. A hostname is
resolved again at connection time, so DNS rebinding or a repointed/stale record
can move traffic away from the IP recorded during discovery.

### Final contract

- When a recorded IP exists, every socket connects to that IP.
- `request_host` is used only for HTTP `Host`, TLS SNI, and certificate identity.
- A saved hostname is never a fallback connection destination.
- A connection failure to the recorded IP is reported as a failure.
- IPv4 and IPv6 literals are normalized and bracketed correctly where needed.

## F2 - SMB Extract Containment

The remote share identifier must remain unchanged for SMB protocol calls.
Local storage uses a separately derived safe, unique segment. Before creating
or opening a destination, its resolved path must be contained by the resolved
download root. This second check covers sanitization errors, symlink edge cases,
and future caller drift.

## F3 - Image Decode Ordering

`Image.open()` is lazy. The viewer must inspect `img.size`, validate dimensions
and multiplication safely, reject images above `max_pixels`, and only then call
`load()`. Pillow's global decompression-bomb threshold does not replace the
project's lower 20-million-pixel limit.

## F4 - ZIP Resource Exhaustion

CPython mitigates common path traversal during `extractall`, but extraction
still permits disk and memory exhaustion. The final design does not extract the
archive tree:

- Inspect no more than 32 members.
- Reject total declared uncompressed size above 256 MiB.
- Consider only root-level regular `.json` and `.csv` members.
- Prefer JSON; otherwise select the first deterministic lexical CSV.
- Reject duplicate eligible member names.
- Reject the selected payload if it is encrypted or cannot be opened by the
  supported standard-library ZIP implementation.
- Reject a selected payload above 128 MiB.
- Stream to a fixed generated temporary filename with an actual-byte cap.

## F5 - Exception Population

AST inspection found exactly 448 product-code handlers whose body is only
`pass`, excluding tests:

| Area | Count |
|---|---:|
| GUI | 375 |
| Experimental | 33 |
| Shared | 29 |
| Commands | 6 |
| Tools | 5 |

The count is not itself proof of 448 defects. Legitimate examples include
`tk.TclError` during teardown, `queue.Empty` polling, `StopIteration` control
flow, and best-effort process cleanup. The defect is the lack of recorded
intent, which makes security-relevant suppression indistinguishable from
intentional silence.

Each handler must be classified as:

- `intentional-silent`
- `should-log-debug`
- `should-surface`

Only the latter two are changed. Logs must not expose secrets, remote file
content, raw URLs containing credentials, or downloaded-data fragments.

## F6 - FTP Controls

CPython has rejected CR/LF in FTP commands since before Python 3.8.
`ftplib.FTP.putline()` raises `ValueError`, so the reported command injection is
not currently exploitable. Explicit rejection of ASCII control characters is
still required for clearer errors and backend independence.

## F7 - SMB Basename

On POSIX, `Path("\\folder\\file.txt").name` returns the entire string.
`PureWindowsPath(...).name` returns `file.txt`. The latter behavior is required
when `preserve_structure=False`.

## F8 - TLS Source Of Truth

Current repo truth contains:

- core `http.verification.allow_insecure_tls`
- a unified-scan persisted GUI key
- a legacy HTTP-dialog persisted GUI key
- temporary scan overrides
- hardcoded browser and probe values
- direct JSON reads in extract paths

The final model is:

1. `SMBSeekConfig` owns the sole persisted application default.
2. App Config exposes and persists that default.
3. A scan dialog may choose a transient per-run override.
4. Browser, probe, classifier, and extract consumers resolve the application
   default through one shared resolver.
5. Runtime code does not read config JSON directly.
6. The default remains `allow_insecure_tls=True` in this wave.
7. Operator docs plainly state the MITM consequence.

## New Deferred Finding N1

The README declares Python 3.8+ support, but Python 3.8 reached end of life on
[2024-10-07](https://peps.python.org/pep-0569/). Raising the minimum may affect
users and dependencies, so it is not bundled into this remediation wave. It
remains an explicitly owned risk with a review trigger before the next
production promotion.
