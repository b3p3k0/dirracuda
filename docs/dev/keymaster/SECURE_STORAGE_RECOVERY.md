# Keymaster secure storage recovery

Status: approved by HI for implementation, 2026-10-13.
Track: full process. PA/DA: primary session. RA: separate reviewer.

## Goal and evidence

Make desktop Keymaster recovery understandable and usable without the old
passphrase. The original manually typed password rejection remains unexplained;
the later empty/unconfigured store resulted from attempted recovery. Reproduced:
disabling an empty secure store requests an impossible unlock and stays enabled.
Baseline: store/window tests, 66 passed in 4.74s. Three isolated exact-input
setup/commit/reopen/decrypt checks passed (ordinary, whitespace, Unicode).

## Approved behavior

- Opening shows Off, Setup needed, Locked, or Unlocked, without password prompts.
- Setup/enable uses one dialog with passphrase and confirmation; unlock uses one
  field. Both have a visibility toggle and preserve exact input.
- Delete all keys and disable requires destructive confirmation but no password.
  Delete all providers and passphrase metadata atomically, persist off, clear
  window session/caches, and do not restart setup. Cancellation changes nothing.
- Enable creates a new passphrase and encrypts existing plaintext rows atomically.
- Missing setup, wrong passphrase, damaged metadata, and storage errors have
  distinct safe messages. Failed operations preserve persisted state and clear
  unsuccessful session state. Old sessions cannot write under new metadata.
- Scope is Keymaster only: applied credentials/config and backups remain. This
  is logical deletion, not forensic erasure. No schema, algorithm, dependency,
  Web UI authentication, CI, or primary-database changes.
- Historical off-mode stores retaining a passphrase require explicit deletion
  before fresh setup; never silently overwrite existing encryption metadata.

## Cards (one at a time, RA acceptance before next)

| Card | Changes | Acceptance |
| --- | --- | --- |
| KSR1 | Store reset with explicit resulting mode; guarded setup and atomic enable; validate metadata/session ownership; regression tests | Empty/locked/all-provider reset persists off; setup/reset failures roll back; exact input survives reopen; stale sessions cannot corrupt new storage |
| KSR2 | Extract desktop security controls and dialog from keymaster_window; explicit states/actions; clear caches and invalidate in-flight refresh results on reset | No opening prompts; setup/unlock correctly dispatched; deletion needs no password; cancellation/failure safe; visibility/focus/layout checked |
| KSR3 | Integration checks, behavior/agent docs, lessons, final review | Desktop/store/Web UI/Analyst compatibility and GUI guardrails pass; isolated ./dirracuda visual exercise; exact evidence and limitations recorded |

## Interfaces and implementation constraints

Store transaction ownership remains with callers; composite security changes
use savepoints without committing caller work. Keep reset's target mode explicit
at every call. Setup must not rotate existing metadata. Existing algorithms and
on-disk schema stay unchanged. Validate unlocked material against current
passphrase metadata before encryption/mutation. Recovery stays available when
passphrase metadata is missing or malformed.

Desktop follows named themes, safe messageboxes and ensure_dialog_focus; Tk
teardown stays on the UI thread. Storage status is separate from transient
operation messages. Keep the GUI/CLI boundary unchanged.

## Validation and rollback

Run ./venv/bin/python -m pytest -q shared/tests/test_keymaster_store.py
gui/tests/test_keymaster_window.py plus new security test modules, then
experimental/webui/tests/test_keymaster_routes.py,
shared/tests/test_analyst_n3_credentials.py and GUI theme/messagebox guardrails.
Mock all external services and use temporary stores. Exercise ./dirracuda with
an isolated home and no live credentials. Do not reset the user's store.

Rollback implementation through a new revert commit, never rewriting history.
Application deletion is irreversible without a separately held backup; the UI
must say what it removes before confirmation. No automated backup of secrets.

## Execution record

- Planning packet recorded before product edits. No original-password fix is
  claimed; recovery and UI defects are the approved implementation scope.
