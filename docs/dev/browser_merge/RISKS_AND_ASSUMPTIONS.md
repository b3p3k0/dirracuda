# Browser Merge Risks and Assumptions

Date: 2026-03-23

## Assumptions

1. We keep protocol-specific backend navigators (`SMBNavigator`, `FtpNavigator`, `HttpNavigator`) and unify at UI/controller layer first.
2. Protocol row identity and delete semantics are already correct and should not be changed in this workstream.
3. Existing browse tests are a baseline, but manual runtime verification remains required.

## Key Risks and Mitigations

## R1: SMB complexity causes regressions

Risk:
- SMB includes share selection and credential derivation not present in FTP/HTTP.

Mitigation:
- Migrate FTP+HTTP first (Card U1/U2), then SMB separately (Card U3).
- Keep SMB fallback path available until SMB parity is validated.

## R2: Hidden coupling in details/action rendering

Risk:
- Browser/probe error payload shapes are not fully normalized (e.g., HTTP probe errors use dicts).

Mitigation:
- Preserve payload contracts first; normalize only with explicit tests and acceptance.

## R3: Path semantics divergence

Risk:
- SMB uses Windows-style paths; FTP/HTTP use POSIX-like paths.

Mitigation:
- Put path handling behind adapter methods and capability flags.
- Avoid direct path arithmetic in unified UI logic.

## R4: Performance regression in UI hot paths

Risk:
- Unification could add extra abstraction overhead in list rendering/probe updates.

Mitigation:
- Keep adapter calls thin and synchronous patterns unchanged.
- Measure responsiveness with manual smoke checks on large directories.

## R5: Partial migration leaves dead callsites

Risk:
- Legacy window classes remain referenced by stale imports/routes.

Mitigation:
- Add explicit cleanup card (U5) and grep-based dead-callsite checks.

## R6: SMB banner source variability

Risk:
- SMB rows may not always carry a straightforward banner field; Shodan metadata shape can vary or be missing.

Mitigation:
- Implement best-effort banner extraction with deterministic fallback order.
- Never fail browse launch on missing/invalid banner data.
- Show a clear placeholder when no banner can be derived.

## R7: Virtual-root state bugs in SMB navigation

Risk:
- Removing share dropdown introduces a second SMB navigation state (host root share list vs in-share path), which can break `Up`/`Refresh`/error handling if not explicit.

Mitigation:
- Use explicit state transitions:
  - root (no active share)
  - share root
  - nested path
- Ensure share-open failure returns/stays at root with clear status.
- Add targeted tests for root -> share -> up -> root transitions.

## Grep Checks for Cleanup

```bash
rg -n "FtpBrowserWindow|HttpBrowserWindow|FileBrowserWindow" gui/components gui/tests
rg -n "ftp_probe_runner|http_probe_runner|probe_runner" gui/utils gui/components
```

## Known Open Decisions (for HI)

1. Should legacy wrapper classes be kept for one release cycle or removed immediately after passing gates?
2. Do we normalize probe error shapes now, or defer to a dedicated follow-up once browser merge is stable?
3. Preferred SMB banner fallback label text when no Shodan banner can be parsed.
