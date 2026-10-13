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
- KSR1 accepted by separate RA on 2026-10-13. Reviewed store/model changes,
  explicit reset callers, and Web UI/Analyst consumers. Setup/enable/reset use
  savepoints; write transactions pin session validation to mutation. Stale root
  and mixed subkeys are rejected. Metadata validation bounds persisted PBKDF2
  work to 100,000–1,000,000 iterations; generated stores retain 600,000.
  RA required the upper bound to prevent corrupted metadata freezing unlock.
- RA validation: `./venv/bin/python -m pytest -q shared/tests/test_keymaster_recovery.py
  shared/tests/test_keymaster_store.py shared/tests/test_analyst_n3_credentials.py
  experimental/webui/tests/test_keymaster_routes.py gui/tests/test_keymaster_window.py`
  → **143 passed, 1 existing Starlette/httpx deprecation warning in 23.57s**.
  After the work-factor regression: `./venv/bin/python -m pytest -q
  shared/tests/test_keymaster_recovery.py` → **28 passed in 8.42s**.
  Independent temporary-store checks confirmed partial metadata-write rollback
  and stale-session rejection after reset through another connection.
  `git diff --check` passed. Desktop behavior remains unchanged until KSR2;
  runtime docs and visual checks close in KSR3. HI review is recommended before
  merging this secrets-related change.
- KSR2 accepted by separate RA on 2026-10-13. Security controls now live in
  `gui/components/keymaster_security.py`: explicit status/actions replace the
  checkbox and automatic prompts. Reset stays off, clears session/rows/credit
  caches, and invalidates prior refresh completions. In-flight requests for the
  current key may finish; their results cannot repopulate the cleared cache.
  `validate_session_keys` is the public store check used before cached-key
  actions. No remaining callers use the former private name.
- RA validation: `xvfb-run -a ./venv/bin/python -m pytest -q
  shared/tests/test_keymaster_store.py shared/tests/test_keymaster_recovery.py
  gui/tests/test_keymaster_window.py gui/tests/test_keymaster_security.py
  gui/tests/test_keymaster_security_rendered.py gui/tests/test_dialog_audit.py
  gui/tests/test_theme_style_guardrail.py gui/tests/test_messagebox_guardrail.py`
  → **125 passed in 18.84s**. DA dialog audit reported **18 results, 0 failed,
  0 uncovered**; RA inspected desktop-light and large-text-dark setup images.
  Fields, visibility control, and action buttons remain legible with scrolling.
  Real Tk regressions cover initial focus, grab, cancellation, and exact input.
  No Blocker or Required findings remain for this card. KSR3 owns the remaining
  runtime-entrypoint exercise and behavior-documentation closeout.
