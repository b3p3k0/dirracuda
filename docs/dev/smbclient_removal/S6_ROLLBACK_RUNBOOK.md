# S6 Rollback Runbook: SMBClient Removal

Date: 2026-03-26
Scope: rollback of pure-Python SMB migration cards `S1`-`S5`

## Rollback Triggers

Execute rollback if any of the following appear after cutover:

1. Discovery misses expected SMB1 targets in legacy mode.
2. Access probe status classification regresses (denied/missing/timeout drift).
3. Production runtime failures tied to the new adapter path.

## Commits in Scope

Rollback target commits (newest first):

1. `066557c` - S5 hard-cutover cleanup
2. `fc8e19c` - S4 share-read probe cutover
3. `0f1dc76` - S3 share enumeration cutover
4. `2ad3210` - S2 discovery cutover
5. `afe8152` - S1 adapter layer introduction

## Rehearsed Rollback (Safe Drill)

A rollback drill was executed in a temporary worktree so the active branch remained untouched.

Commands used:

```bash
git worktree add /tmp/smbseek_s6_rollback HEAD
cd /tmp/smbseek_s6_rollback
git revert --no-commit 066557c fc8e19c 0f1dc76 2ad3210 afe8152
rg -n "smbclient" commands/discover commands/access shared/workflow.py README.md
git status --short
cd -
git worktree remove /tmp/smbseek_s6_rollback --force
```

Observed drill outcome:

- `git revert --no-commit ...` succeeded without conflicts.
- `rg -n "smbclient" ...` returned expected legacy hits (confirming rollback restored pre-cutover paths).
- Worktree cleanup completed and temporary checkout was removed.

## Production Rollback Procedure

Run from your working branch (no pending unrelated changes staged):

```bash
git revert --no-edit 066557c fc8e19c 0f1dc76 2ad3210 afe8152
```

Then validate:

```bash
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ shared/tests/ -q --tb=short
python3 -m py_compile commands/discover/auth.py commands/access/share_enumerator.py commands/access/share_tester.py
rg -n "smbclient" commands/discover commands/access shared/workflow.py README.md
```

Expected recovery outcome:

1. Legacy `smbclient` code paths are restored.
2. Previous discovery/access behavior is reinstated.
3. Test suite remains green before any push/deploy step.

## Forward Re-Apply After Rollback (Optional)

If rollback stabilizes production and you need to re-apply cutover later, reintroduce cards one-by-one (S1 → S5) and validate each card gate before proceeding.
