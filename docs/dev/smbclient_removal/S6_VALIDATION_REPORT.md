# S6 Validation Report: SMBClient Removal

Date: 2026-03-26
Branch: `development`
Validation baseline commit: `066557c`

## Summary

Automated validation gates for the pure-Python SMB cutover are passing.

- Automated gate status: PASS
- Runtime `smbclient` dependency gate: PASS (no matches in runtime SMB workflow paths)
- Manual HI matrix: PENDING (requires known SMB lab targets)

## Automated Validation

### 1) Full shared + GUI regression

Command:

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
```

Result:

- PASS
- `463 passed in 6.49s`

### 2) Compile-time guard for touched transport modules

Command:

```bash
python3 -m py_compile shared/smb_adapter.py commands/discover/auth.py commands/access/share_enumerator.py commands/access/share_tester.py
```

Result:

- PASS
- no output (successful compile)

### 3) Runtime dependency grep gate (`smbclient`)

Command:

```bash
rg -n "smbclient" commands/discover commands/access shared/workflow.py README.md
```

Result:

- PASS
- no matches (exit code `1` from `rg`, expected for zero results)

## Cautious/Legacy Contract Matrix

| Contract | Expected | Status |
|---|---|---|
| Cautious mode transport | Signed SMB2+/SMB3-only behavior preserved | PENDING HI |
| SMB1 rejection in cautious mode | SMB1-only targets are not authenticated/discovered | PENDING HI |
| Legacy mode SMB1 coverage | SMB1-capable targets still discoverable/testable | PENDING HI |
| Access share enumeration/probe | Pure-Python (`SMBAdapter`) only; no subprocess path | PASS (automated) |

## Manual HI Validation Needed

1. Run one cautious scan against known SMB2/3 signed-capable hosts.
2. Run one legacy scan including known SMB1-capable hosts.
3. Confirm SMB1 hosts appear only in legacy mode.
4. Run access verification on known share outcomes (accessible/denied/missing) and confirm status messaging.

Expected manual outcome:

- Cautious mode excludes SMB1-only hosts.
- Legacy mode includes SMB1 hosts.
- Access statuses remain deterministic (`ACCESS_DENIED`, `NT_STATUS_BAD_NETWORK_NAME`, timeout family).
