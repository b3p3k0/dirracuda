# Security Remediation Specification

Status: decision complete for Claude card planning

## Objective

Reduce risk when Dirracuda communicates with attacker-controlled SMB, FTP, and
HTTP services or processes untrusted files, while preserving existing protocol
coverage, GUI-to-CLI boundaries, and operator workflows.

## Threat Model

Assume a remote target can intentionally:

- return hostile redirects and directory listings;
- control SMB share and remote file names;
- change DNS records after discovery;
- serve malformed or resource-exhausting images and archives;
- cause cleanup, permission, logging, or UI-update operations to fail;
- present invalid or self-signed TLS certificates.

Assume imported ZIP files and database-exchange artifacts are untrusted.

Do not assume the process runs as root. The documented VM, VPN, and quarantine
guidance are compensating controls, not substitutes for application controls.

## Required Behaviors

### HTTP transport

- All target HTTP operations use a shared transport abstraction.
- The abstraction supports bounded response reads and streamed downloads.
- Redirect policy is same-origin only, maximum three hops.
- Same-origin means identical lowercase scheme, normalized hostname, and
  effective port after default-port normalization.
- Userinfo in redirect URLs is rejected.
- Ambient proxy environment variables are ignored for target traffic.
- Error reasons are stable enough for tests and user-facing summaries.

### Endpoint identity

- `connect_ip` is the socket destination whenever a recorded IP exists.
- `request_host` may set `Host`, SNI, and certificate identity only.
- No DNS fallback occurs after a failed IP connection.
- Relative redirects retain the pinned connection destination.
- Hostname-only callers remain supported only when no recorded IP exists and
  the caller explicitly uses the hostname as its primary target.

### TLS policy

- Canonical persisted key: `http.verification.allow_insecure_tls`.
- Default remains `true`.
- `SMBSeekConfig` performs loading, coercion, and defaulting.
- App Config is the only UI that changes the persisted default.
- Scan dialogs may supply a transient override in the scan request.
- Scan-dialog close/reopen persistence must not create another policy source.
- Browser, probe, classifier, and extract calls use the shared resolver.
- Strict mode verifies certificate chain and hostname.
- Insecure mode disables both checks and is documented as MITM-vulnerable.

### SMB local paths

- Remote share strings remain unchanged for remote protocol calls.
- Local share labels are one sanitized path segment.
- Colliding sanitized labels receive deterministic suffixes.
- Every final resolved destination is contained by the resolved download root.
- Containment failure stops that file before parent creation or file open.

### Images

- Dimensions are checked before full pixel decode.
- Zero, negative, malformed, or over-limit dimensions fail closed.
- Existing viewer error handling remains user-readable.

### ZIP imports

- Archive trees are never extracted.
- Maximum member count: 32.
- Maximum total declared uncompressed size: 256 MiB.
- Maximum selected payload declared and streamed size: 128 MiB.
- Only root-level regular `.json` or `.csv` members are eligible.
- JSON is preferred; otherwise lexical filename order is used.
- Directory and nested members are ineligible; an archive with no eligible
  payload is rejected.
- Duplicate eligible member names are rejected as ambiguous.
- Selection occurs before encryption handling; an encrypted selected payload
  is rejected rather than silently falling through to another member.
- A selected payload that the standard-library ZIP reader cannot open is
  rejected with an actionable validation error.

### FTP paths

- Reject C0 controls and DEL in remote paths before `SIZE` or `RETR`.
- The rejection error identifies invalid path syntax without echoing control
  characters.
- Existing anonymous FTP behavior remains unchanged.

### SMB basename

- `preserve_structure=False` uses Windows path semantics.
- Empty or root-only names fail before file creation.

### Exception audit

- Baseline is fixed at 448 pass-only handlers on commit `4320614`.
- Every handler receives a reviewed classification and rationale.
- `intentional-silent` handlers remain silent and gain a concise comment only
  where intent is not self-evident.
- `should-log-debug` handlers use the established local logger and sanitized
  context.
- `should-surface` handlers return, raise, record, or display an actionable
  failure through the owning layer's existing contract.
- No batch contains more than 40 baseline handlers.

## Non-Goals

- No new dependency.
- No database schema or migration.
- No auth or Web UI security redesign.
- No GUI-to-workflow direct calls.
- No change to SMB cautious/legacy protocol policy.
- No blanket replacement of exception handlers.
- No switch to strict TLS by default.
- No Python minimum-version change.
- No live-network security tests.

## Compatibility

- Existing CLI arguments and stdout parsing remain stable.
- Existing persisted core config remains readable.
- If the raw runtime config explicitly contains the canonical TLS key, it wins.
- If the canonical key is absent, migrate once from
  `unified_scan_dialog.allow_insecure_tls`, then
  `http_scan_dialog.allow_insecure_tls`, then the default `true`.
- Retired GUI TLS keys may remain on disk for compatibility but are no longer
  read or written after migration.
- HTTP virtual-host access must continue through pinned-IP plus Host/SNI.
- Existing import CSV/JSON behavior remains unchanged outside ZIP wrapping.
- Existing browser and extract size limits continue to apply in addition to
  the new archive and image controls.

## Acceptance

The wave is complete only when:

1. Every implementation card is accepted by RA.
2. All 448 exception entries have a final classification and evidence.
3. Every non-intentional exception entry is remediated.
4. Focused adversarial tests pass.
5. Full automated gates pass.
6. Required HI manual tests pass.
7. README and Technical Reference match runtime truth.
8. Remaining risks and Python 3.8 deferral have owners and review triggers.
