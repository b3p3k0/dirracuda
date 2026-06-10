# Security Remediation Architecture

Status: implementation design contract

## Shared HTTP Transport

Create one shared transport module under `shared/`. Exact symbol names are
chosen in the approved C1/C2 plan, but the responsibilities are fixed.

```text
verifier / browser / probe / extractor
                 |
                 v
       shared HTTP transport
       - target identity
       - proxy disabled
       - redirect policy
       - TLS context
       - bounded read/stream
                 |
                 v
          recorded endpoint
```

### Request inputs

- `connect_host`: recorded IP when available.
- `request_host`: optional virtual-host name.
- `scheme`
- `port`
- `path`
- timeout
- TLS policy
- maximum read or stream budget

The transport must not accept a prebuilt arbitrary URL from callers that can
silently change destination identity.

### Redirect algorithm

1. Build the initial URL identity from scheme, logical request host, and port.
2. Connect using the pinned `connect_host`.
3. On `Location`, resolve against the current logical URL.
4. Normalize scheme, IDNA/lowercase hostname, and effective port.
5. Reject if any normalized identity field changes.
6. Reject userinfo, unsupported schemes, invalid ports, or missing authority.
7. Repeat for at most three redirects.
8. Preserve pinned socket destination and update only the path/query.

Redirect responses do not automatically become successful directory listings.
The final response still passes existing status and content validation.

### Host and TLS behavior

For an HTTPS virtual host:

```text
TCP destination: recorded IP
HTTP Host:       saved hostname[:non-default-port]
TLS SNI:         saved hostname
Cert identity:   saved hostname in strict mode
```

If no saved hostname exists, the IP is used consistently. A failed IP
connection is not retried through DNS.

Python's high-level `urllib` APIs do not cleanly separate TCP destination from
SNI/certificate hostname in every path. The C2 plan must confirm a standard
library implementation before code begins. If the standard library cannot
provide the required separation without unsafe monkeypatching, C2 stops for
HI/RA architecture review rather than weakening pinning or certificate checks.

## TLS Policy Resolution

### Persisted authority

The sole persisted application default is:

```text
http.verification.allow_insecure_tls
```

`SMBSeekConfig` owns validation and coercion. No runtime consumer opens a JSON
file to obtain this value.

### Legacy preference migration

Migration precedence is fixed:

1. An explicitly present canonical runtime-config value wins.
2. Otherwise use `unified_scan_dialog.allow_insecure_tls` when present.
3. Otherwise use `http_scan_dialog.allow_insecure_tls` when present.
4. Otherwise use `true`.

The selected value is persisted through the existing config-store abstraction.
Retired GUI keys are no longer read or written; they need not be deleted from
the preferences file.

### User interfaces

- App Config reads and writes the persisted default.
- Unified and legacy HTTP scan dialogs initialize from that default.
- A dialog choice is carried in the returned scan request as a transient
  per-run override.
- Dialog preference files must not persist independent TLS policy.

### Consumers

The shared resolver is used by:

- HTTP discovery verification
- HTTP browser list/read/download
- HTTP probe paths
- HTTP bulk extraction
- SearXNG/sidecar HTTP classification where it assesses discovered targets

Web UI server TLS settings are unrelated and remain under
`experimental.webui` configuration.

## SMB Extraction Containment

Maintain two identities:

```text
remote_share = exact server value used by SMB calls
local_share  = sanitized unique segment used on disk
```

Build the destination using `local_share` and sanitized relative parts. Resolve
the prospective path with `strict=False`, resolve the download root, and require
the destination to be relative to the root. A symlinked parent that escapes the
root therefore fails before writing.

## ZIP Import Flow

```text
open archive
  -> inspect metadata and enforce member/total caps
  -> select one eligible root-level payload
  -> open member stream
  -> copy in chunks to generated fixed temp filename
  -> enforce actual-byte cap while copying
  -> parse through existing CSV/JSON reader
  -> temporary directory cleanup
```

No archive-provided path is used as a filesystem destination.

## Exception Audit Data Flow

`EXCEPTION_AUDIT_PLAN.md` is the immutable baseline inventory. During E0, Claude
copies it into a working classification ledger with:

```text
inventory id
baseline path and line
current path and line
owner operation
classification
rationale
remediation
tests/evidence
card
status
```

Later batches update code and the ledger together. Line drift is tracked by
inventory ID and surrounding operation, not by assuming baseline line numbers
remain current.
