# SMB Pure-Python Migration Task Cards (Dirracuda)

Date: 2026-03-26
Status: Draft (locked decisions captured from HI)
Execution model: one small issue/card at a time, with explicit PASS/FAIL gates.

## Locked Decisions (2026-03-26)

1. SMB1 support in discovery is essential and non-negotiable.
2. Cautious mode must remain strict (`signed SMB2+/SMB3 only` behavior).
3. Legacy `smbclient`-labeled metadata/output should be cleaned.

## Card S0: Contract Inventory + Rollout Plan (Plan Only)

Status:
- Completed (2026-03-26)

Issue:
- SMB operations currently mix `smbprotocol` and `smbclient` subprocess calls, which creates runtime dependency drift and uneven behavior across modes.

Scope:
- Planning artifacts only; no runtime code edits.

Deliverables:
1. `docs/dev/smbclient_removal/TASK_CARDS.md` (this file)
2. `docs/dev/smbclient_removal/CONTRACT_MATRIX.md`
3. `docs/dev/smbclient_removal/RISK_REGISTER.md`

Acceptance:
1. Every SMB callsite currently using `smbclient` is mapped to a pure-Python replacement path.
2. Cautious vs legacy contract is explicitly defined for discovery and access phases.
3. Includes rollback plan and known-failure prevention checklist.

Validation:
```bash
rg -n "smbclient|--client-protection|--max-protocol|parse_share_list|NT_STATUS" commands shared README.md
```

HI test needed:
- No (artifact review only).

## Card S1: Pure-Python SMB Adapter Layer (Code)

Status:
- Completed (2026-03-26)

Issue:
- SMB logic is duplicated and transport-specific details leak into operations, making cutover risky.

Scope:
- Add a centralized SMB adapter layer that exposes protocol-agnostic methods for discovery/access.
- No behavior change intended yet (bridge layer first).

Likely files:
1. New: `shared/smb_adapter.py`
2. `commands/discover/auth.py`
3. `commands/access/share_enumerator.py`
4. `commands/access/share_tester.py`
5. `commands/access/operation.py`

Acceptance:
1. Adapter provides a stable API for:
   - auth probing (anonymous / guest blank / guest guest)
   - share enumeration
   - share access probe (`ls` equivalent)
2. Adapter supports both transport backends:
   - `smbprotocol` path for strict cautious-mode auth guarantees
   - `impacket` path for SMB1-inclusive legacy behavior
3. Returns normalized structured results (no CLI text parsing dependency).

Validation:
```bash
python3 -m py_compile shared/smb_adapter.py commands/discover/auth.py commands/access/share_enumerator.py commands/access/share_tester.py commands/access/operation.py
./venv/bin/python -m pytest shared/tests/ -q
```

HI test needed:
- No (internal bridge card).

## Card S2: Discovery Cutover (Remove `smbclient` Fallback)

Status:
- Completed (2026-03-26)

Issue:
- Discovery currently falls back to `smbclient -L` when `smbprotocol` auth fails.

Scope:
- Replace discovery fallback path with pure-Python logic while preserving SMB1 coverage in legacy mode.

Likely files:
1. `commands/discover/auth.py`
2. `commands/discover/operation.py`
3. `shared/tests/test_discover_auth_fallback.py` (or replacement tests)
4. New tests: `shared/tests/test_discover_auth_pure_python.py`

Acceptance:
1. Discovery has zero dependency on system `smbclient`.
2. Legacy mode still discovers SMB1-capable targets.
3. Cautious mode behavior remains strict:
   - reject SMB1-only targets
   - require signed SMB2+/3 session semantics
4. `auth_method` no longer appends `(smbclient)`.

Validation:
```bash
./venv/bin/python -m pytest shared/tests/test_discover_auth_fallback.py -q
./venv/bin/python -m pytest shared/tests/test_discover_auth_pure_python.py -q
rg -n "smbclient" commands/discover
```

HI test needed:
- Yes.
- Steps:
1. Run one cautious scan against known SMB2/3 signed-capable host set.
2. Run one legacy scan including known SMB1-capable host set.
3. Confirm SMB1 hosts appear only in legacy mode and not in cautious mode.

## Card S3: Access Share Enumeration Cutover (No CLI Parsing)

Status:
- Completed (2026-03-26)

Issue:
- Access phase enumerates shares by parsing `smbclient -L` output text, which is brittle and subprocess-dependent.

Scope:
- Replace share enumeration with pure-Python API calls (no stdout parsing).

Likely files:
1. `commands/access/share_enumerator.py`
2. `commands/access/operation.py`
3. New tests: `shared/tests/test_access_share_enumerator_pure_python.py`

Acceptance:
1. Shares are enumerated via adapter/API, not command output parsing.
2. Existing share filtering semantics are preserved:
   - non-admin shares only by default
   - disk shares only for access testing flow
3. Performance does not regress on host loops (connection/session reuse where possible).

Validation:
```bash
python3 -m py_compile commands/access/share_enumerator.py commands/access/operation.py
./venv/bin/python -m pytest shared/tests/test_access_share_enumerator_pure_python.py -q
./venv/bin/python -m pytest shared/tests/ -q
```

HI test needed:
- Yes.
- Steps:
1. Run access step on a host with multiple shares.
2. Confirm share counts match pre-cutover baseline (excluding expected label cleanup).

## Card S4: Access Share-Read Testing Cutover + Error Normalization

Issue:
- Share access checks currently depend on `smbclient //host/share -c ls` and textual `NT_STATUS` parsing.

Scope:
- Move access testing to pure Python and normalize errors/status codes for stable UI/reporting behavior.

Likely files:
1. `commands/access/share_tester.py`
2. `shared/smb_adapter.py`
3. `commands/access/operation.py`
4. New tests: `shared/tests/test_access_share_tester_pure_python.py`

Acceptance:
1. Read-access determination no longer relies on subprocess return codes.
2. Error classification remains deterministic (`ACCESS_DENIED`, `BAD_NETWORK_NAME`, timeout, etc.).
3. No behavior drift outside requested cutover (same user-visible semantics for pass/fail classification).

Validation:
```bash
python3 -m py_compile commands/access/share_tester.py shared/smb_adapter.py
./venv/bin/python -m pytest shared/tests/test_access_share_tester_pure_python.py -q
./venv/bin/python -m pytest shared/tests/ -q
```

HI test needed:
- Yes.
- Steps:
1. Probe hosts with known outcomes: accessible share, denied share, missing share.
2. Confirm UI messages/status rows remain correct and actionable.

## Card S5: Hard Cutover Cleanup (Code + Docs)

Issue:
- After migration, dead `smbclient` code paths and legacy labels can linger and reintroduce drift.

Scope:
- Remove obsolete subprocess checks/builders, clean metadata labels, and update docs/dependency messaging.

Likely files:
1. `commands/discover/auth.py`
2. `commands/discover/operation.py`
3. `commands/access/share_enumerator.py`
4. `commands/access/share_tester.py`
5. `commands/access/operation.py`
6. `README.md`
7. Any affected tests/docs

Acceptance:
1. No runtime SMB workflow path requires system `smbclient`.
2. Legacy labels/suffixes tied to `smbclient` are removed from new writes/output.
3. README/setup docs no longer state `smbclient` as required for SMB discovery/access core paths.

Validation:
```bash
rg -n "smbclient" commands/discover commands/access shared/workflow.py
rg -n "smbclient" README.md docs/dev/smbclient_removal
./venv/bin/python -m pytest shared/tests/ gui/tests/ -q --tb=short
```

HI test needed:
- Yes.
- Steps:
1. Fresh environment without `smbclient` binary installed.
2. Run discovery + access flow in both cautious and legacy modes.
3. Confirm expected behavior and no missing-tool errors.

## Card S6: Full Validation + Rollback Drill

Issue:
- Transport migration needs explicit proof that cautious/legacy contracts still hold and can be rolled back safely.

Scope:
- End-to-end regression validation, manual sign-off run, and rollback rehearsal.

Deliverables:
1. `docs/dev/smbclient_removal/S6_VALIDATION_REPORT.md`
2. `docs/dev/smbclient_removal/S6_ROLLBACK_RUNBOOK.md`

Acceptance:
1. Automated and manual gates reported with exact commands + PASS/FAIL.
2. Includes cautious-mode and legacy-mode matrix with SMB1-focused verification.
3. Rollback path tested and documented with expected recovery outcomes.

Validation:
```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
python3 -m py_compile shared/smb_adapter.py commands/discover/auth.py commands/access/share_enumerator.py commands/access/share_tester.py
rg -n "smbclient" commands/discover commands/access shared/workflow.py README.md
```

HI test needed:
- Yes (final sign-off run).

## Research References (Primary Sources)

1. smbprotocol README (SMB2/3 scope, signing/encryption, high/low-level APIs):  
   https://raw.githubusercontent.com/jborean93/smbprotocol/master/README.md
2. Impacket `SMBConnection` API (SMB1/2/3 wrapper, `listShares`, `listPath`, dialect/auth methods):  
   https://raw.githubusercontent.com/fortra/impacket/master/impacket/smbconnection.py
3. Impacket SMB client example (share enumeration and shell operations):  
   https://raw.githubusercontent.com/fortra/impacket/master/impacket/examples/smbclient.py
4. Samba `smbclient` man page (current CLI flags/behavior being replaced):  
   https://www.samba.org/samba/docs/current/man-html/smbclient.1.html
