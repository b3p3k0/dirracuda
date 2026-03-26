# SMBClient Removal Risk Register

Date: 2026-03-26
Status: S0 Draft
Scope: Pure-Python SMB cutover (`smbclient` subprocess removal).

## Risk Table

| ID | Risk | Severity | Phase | Detection Signal | Mitigation | Owner | Status |
|----|------|----------|-------|------------------|------------|-------|--------|
| R1 | Legacy mode loses SMB1 discovery coverage | HIGH | S2 | Known SMB1 fixture host no longer discovered in legacy mode | Add mandatory SMB1 legacy gate before and after S2; fail card if mismatch | AI+HI | Open |
| R2 | Cautious mode no longer enforces strict contract | HIGH | S2-S4 | SMB1/unsigned target accepted in cautious mode | Keep strict cautious checks in adapter; add negative tests + manual gate on known incompatible hosts | AI+HI | Open |
| R3 | Share enumeration output drifts from pre-cutover semantics | HIGH | S3 | Share counts/filtered names differ unexpectedly on baseline hosts | Build parity assertions against fixture output and host baselines before deleting parser path | AI | Open |
| R4 | Error/status categorization drifts in UI/reporting | HIGH | S4 | Access rows show generic errors where specific codes existed (`ACCESS_DENIED`, `BAD_NETWORK_NAME`, timeout) | Introduce canonical error mapping contract with explicit tests | AI | Open |
| R5 | Hidden `smbclient` runtime dependency remains | MEDIUM | S5 | Running without `smbclient` still throws missing binary errors | Repo-wide grep gates and no-binary smoke run | AI+HI | Open |
| R6 | Performance regression in access hot path | MEDIUM | S3-S4 | Access scans materially slower / timeouts increase on same host set | Prefer connection/session reuse; avoid per-share reconnect loops where safe | AI | Open |
| R7 | Label cleanup breaks downstream assumptions | MEDIUM | S2-S5 | Parsing/reporting tools relying on `(smbclient)` suffix fail | Remove suffix only for new writes; document transition and verify affected queries/tests | AI+HI | Open |
| R8 | Rollback ambiguity after multi-card changes | MEDIUM | S2-S6 | Unable to identify safe revert point quickly | One commit per card (already locked), explicit commit hash in card completion report | AI | Open |
| R9 | Documentation drift vs runtime behavior | LOW | S5 | README still mentions `smbclient` as required after cutover | Include doc grep gate in S5 acceptance | AI | Open |
| R10 | Test suite gives false confidence without manual runtime checks | HIGH | S2-S6 | Automated pass but VM/manual behavior fails | Maintain required HI runtime gates per card (cautious + legacy matrix) | AI+HI | Open |

## Rollback Triggers

Rollback is required if any of the following occur after a card lands:

1. Legacy mode no longer discovers known SMB1 targets.
2. Cautious mode accepts SMB1/unsigned behavior.
3. Access workflow loses share enumeration on previously stable hosts.
4. GUI/CLI SMB workflows fail in environments without `smbclient`.
5. Automated failure count increases without intentional, reviewed test updates.

## Rollback Strategy (Per Card)

Card-level rollback uses one-commit-per-card reverts.

### S1 rollback
```bash
git revert <S1-commit-hash>
```

### S2 rollback
```bash
git revert <S2-commit-hash>
```

### S3 rollback
```bash
git revert <S3-commit-hash>
```

### S4 rollback
```bash
git revert <S4-commit-hash>
```

### S5 rollback
```bash
git revert <S5-commit-hash>
```

### S6 rollback
S6 is validation/reporting only; no runtime rollback expected.

## Pre-Rollback Safety Checks

Run before any revert:

```bash
git status --short
git log --oneline -10
./venv/bin/python -m pytest shared/tests/ -q
```

If database behavior is involved in a failure:

```bash
sqlite3 smbseek.db "PRAGMA integrity_check;"
```

## Known-Failure Prevention Checklist

Apply this checklist before marking any card complete:

- [ ] Verified active runtime path, config path, and DB path.
- [ ] Verified cautious and legacy contracts explicitly (not inferred).
- [ ] Verified command examples against actual file/function names.
- [ ] Ran targeted tests for changed code paths.
- [ ] Captured exact PASS/FAIL outputs and commands in card report.
- [ ] Confirmed no unexpected `smbclient` subprocess path remains (when applicable).
- [ ] Recorded assumptions/risk notes for any deferred edge case.

## Validation Gate (S0)

```bash
rg -n "smbclient|--client-protection|--max-protocol|parse_share_list|NT_STATUS" commands shared README.md
```

## Primary Research References

1. smbprotocol README:  
   https://raw.githubusercontent.com/jborean93/smbprotocol/master/README.md
2. Impacket SMBConnection source:  
   https://raw.githubusercontent.com/fortra/impacket/master/impacket/smbconnection.py
3. Samba smbclient man page:  
   https://www.samba.org/samba/docs/current/man-html/smbclient.1.html
