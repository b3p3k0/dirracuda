# SMB Transport Contract Matrix

Date: 2026-03-26
Status: S0 Draft (runtime contracts mapped)
Scope: `smbclient` dependency removal for SMB discovery/access workflows.

## Locked Constraints

1. SMB1 discovery support is mandatory in legacy mode.
2. Cautious mode remains strict (`signed SMB2+/SMB3 only` contract).
3. Legacy `smbclient` labels/suffixes are removed from new writes/output.

## Runtime Contracts

| ID | Area | Current Contract | Current Implementation | Target Contract | Target Implementation | Mode Notes | Status |
|----|------|------------------|------------------------|-----------------|-----------------------|-----------|--------|
| D0 | Discovery primary auth | Probe auth in order: Anonymous -> Guest/Blank -> Guest/Guest | `commands/discover/auth.py:test_single_host()` + `test_smb_auth()` | Preserve auth probe order and result shape | Keep `smbprotocol` auth-first path in adapter | Cautious and legacy both keep same auth-method order | Planned |
| D1 | Discovery fallback trigger | If primary auth fails and `smbclient` exists, run fallback probe path | `commands/discover/auth.py:test_smb_alternative()` | Fallback remains but must be pure Python | Adapter-based fallback path (no subprocess) | Legacy fallback must include SMB1 targets | Planned |
| D2 | Discovery binary dependency | Runtime checks shell binary (`smbclient --help`) | `commands/discover/auth.py:check_smbclient_availability()` + `commands/discover/operation.py` | No external binary requirement for SMB discovery/auth | Remove binary checks; rely on Python deps only | Fail fast only on missing Python libs | Planned |
| D3 | Cautious protocol gate (discovery) | Cautious mode limits fallback probe to SMB2+/3 via CLI flags | `_build_smbclient_probe_cmd()` in `commands/discover/auth.py` | Cautious contract stays strict without CLI flags | Enforce via `smbprotocol` session params (`require_signing`, SMB2+/3 dialect set) | Reject SMB1-only targets in cautious mode | Planned |
| D4 | Discovery auth cache contract | Cache fallback auth result per host | `DiscoverOperation._smbclient_auth_cache` | Cache behavior retained, transport-agnostic naming | Adapter-level cache/state with existing semantics | Same cache hit behavior in both modes | Planned |
| D5 | Auth label contract | Fallback writes `auth_method` suffix `(smbclient)` | `commands/discover/auth.py` | Remove transport-specific suffix from new records | Write canonical auth method only (`Anonymous`, `Guest/Blank`, `Guest/Guest`) | Clean labels per HI decision | Planned |
| A0 | Access host processing | Access phase uses auth method from discovery row | `commands/access/operation.py:parse_auth_method()` | Preserve auth parsing and host flow shape | Keep method parsing semantics (transport-independent) | No behavior drift outside transport swap | Planned |
| A1 | Share enumeration entrypoint | Enumerate shares via `smbclient -L` + stdout parsing | `commands/access/share_enumerator.py:enumerate_shares()` + `parse_share_list()` | Enumerate shares via API return data only | Adapter uses `impacket.SMBConnection.listShares()` (or equivalent) | Must preserve legacy SMB1 coverage | Planned |
| A2 | Share filtering contract | Keep non-admin disk shares only (`!name.endswith('$')` and disk type) | `commands/access/share_enumerator.py:parse_share_list()` | Preserve filter semantics without text parser | Filter typed share objects from API response | Same output shape (`List[str]`) | Planned |
| A3 | Access probe contract | Per-share probe uses `smbclient //host/share -c ls` | `commands/access/share_tester.py:test_share_access()` | Probe access via pure-Python share operations | Adapter uses tree connect + root list API | Preserve pass/fail semantics and timeouts | Planned |
| A4 | Error normalization contract | Parse `NT_STATUS_*` from CLI stderr/stdout and map friendly messages | `commands/access/share_tester.py:_format_smbclient_error()` | Preserve stable error categories independent of transport | Map exceptions/status codes to canonical categories (`ACCESS_DENIED`, `BAD_NETWORK_NAME`, `TIMEOUT`, etc.) | User-facing messages remain deterministic | Planned |
| A5 | Access binary dependency | Runtime warns and limits scan when `smbclient` missing | `commands/access/operation.py` + `share_enumerator.py` | Access flow must run with Python deps only | Remove binary availability gate | No "limited due to missing smbclient" path | Planned |

## Non-Runtime Contracts

| ID | Area | Current Contract | Current Implementation | Target Contract | Status |
|----|------|------------------|------------------------|-----------------|--------|
| N1 | README setup contract | Setup declares `smbclient` as required system dependency | `README.md` setup + dependency table | Update docs to Python-only SMB workflow dependency model | Planned |
| N2 | Unit-test contract | Tests assert subprocess command composition for `smbclient` fallback | `shared/tests/test_discover_auth_fallback.py` | Replace with transport-agnostic adapter behavior tests | Planned |
| N3 | Troubleshooting messaging | Runtime/debug output references `smbclient` transport | `commands/discover/*`, `commands/access/*`, README | Update wording to backend-neutral pure-Python transport | Planned |

## Cautious vs Legacy Contract (Target)

| Behavior | Cautious Mode (strict) | Legacy Mode |
|----------|-------------------------|-------------|
| SMB1 discovery | Rejected | Allowed and required |
| Signing semantics | Enforced (signed SMB2+/3 contract) | Not required |
| Dialect floor | SMB2+ | SMB1+ |
| Transport policy | Pure-Python only (no shell calls) | Pure-Python only (no shell calls) |
| Auth probe order | Anonymous -> Guest/Blank -> Guest/Guest | Anonymous -> Guest/Blank -> Guest/Guest |

## Implementation Notes

1. Discovery and access must remain shape-compatible with current `DiscoverResult` / `AccessResult` consumers.
2. Remove transport identity from persisted/user-visible auth labels.
3. Keep deterministic behavior for empty/denied/missing-share outcomes; do not silently widen success criteria.
4. Prefer connection reuse in access loops to avoid performance regressions on hot paths.

## Primary Research References

1. smbprotocol README (SMB2/3 scope, signing, encryption):  
   https://raw.githubusercontent.com/jborean93/smbprotocol/master/README.md
2. Impacket SMBConnection API (`listShares`, `listPath`, SMB1/2/3 wrapper):  
   https://raw.githubusercontent.com/fortra/impacket/master/impacket/smbconnection.py
3. Samba smbclient man page (legacy CLI behavior being replaced):  
   https://www.samba.org/samba/docs/current/man-html/smbclient.1.html
