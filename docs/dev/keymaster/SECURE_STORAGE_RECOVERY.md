# Keymaster secure storage recovery

Status: KSR1–KSR5 implemented and accepted by a separate RA, 2026-10-13.
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

## KSR3 final validation (DA)

The automated runtime exercise exposed a state-transition layout issue missed
by the initial-state screenshots: changing Set passphrase to Enable secure
storage did not remeasure the action row. The follow-up explicitly schedules
row layout on security-state rendering. Real Tk tests now compare allocated and
requested button widths across all five states; the audit includes transition
to Off. No previously accepted commits were rewritten.

Final combined command, after the layout follow-up:

```bash
xvfb-run -a ./venv/bin/python -m pytest -q \
  shared/tests/test_keymaster_store.py \
  shared/tests/test_keymaster_recovery.py \
  shared/tests/test_analyst_n3_credentials.py \
  experimental/webui/tests/test_keymaster_routes.py \
  gui/tests/test_keymaster_window.py \
  gui/tests/test_keymaster_security.py \
  gui/tests/test_keymaster_security_rendered.py \
  gui/tests/test_dialog_audit.py \
  gui/tests/test_theme_style_guardrail.py \
  gui/tests/test_messagebox_guardrail.py \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_experimental_sidecar.py
```

Output: **267 passed, 1 warning in 30.49s**. The warning is the existing
Starlette/httpx deprecation; no dependency changes were made.

```bash
./venv/bin/python scripts/run_dialog_audit.py \
  --scenario keymaster,keymaster-off,keymaster-setup,keymaster-unlock \
  --theme both --output /tmp/dirracuda-keymaster-recovery-audit-final
```

Output: **24 results; 0 failed; 0 uncovered**. DA inspected the updated Off
screenshots, including large-text dark mode. Controls are legible and setup
content remains accessible by scrolling.

Actual entrypoint exercise: subprocess argument list
`['xvfb-run', '-a', './dirracuda', '--mock', '--backend-path', '/home/kevin/DEV/dirracuda']`.
Temporary `sitecustomize.py` instrumentation redirected `get_paths()` to
disposable config/databases, blocked socket connections, and made background
workers inert. Tk callbacks entered synthetic passphrases and invoked controls.
This was an **automated runtime exercise plus screenshot inspection**, not an
unaided manual run on HI's desktop. Output, exit 0:

```text
PASS: ./dirracuda mock runtime; setup cancellation, reset cancellation, password-free disable, enable, reopen, wrong/correct unlock, focus, reveal, and populated reset
```

Ephemeral artifacts/harness: `/tmp/dirracuda-keymaster-runtime-final-ld7bsq45/`.
The final run also asserted Enable secure storage's allocated width meets its
requested width. `git diff --check` returned no output (exit 0).

Limits: Linux/Xvfb only; no live provider calls, live-store reset, forensic
erasure validation, or reproduction of the original password rejection.
Applied configuration credentials remain outside reset scope. HI should review
the changed security workflow before merge. Nothing was pushed.

## Final RA acceptance

KSR3 accepted on 2026-10-13, including the focused layout follow-up discovered
through the real entrypoint. RA reviewed the docs against runtime, inspected the
isolated harness and final Off screenshot, and verified the action label fits.
The earlier initial-state audit did not cover this transition; the expanded
audit and five-state bounds regression now do.

Independent command:

```bash
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_keymaster_security_rendered.py \
  gui/tests/test_dialog_audit.py \
  experimental/webui/tests/test_keymaster_routes.py \
  shared/tests/test_analyst_n3_credentials.py
```

Output: **69 passed, 1 existing warning in 13.16s**. Final diff whitespace check
passed. All three cards are accepted; no Blocker or Required findings remain.
This is acceptance of the recovery controls and storage safeguards, not a claim
to have reproduced or diagnosed the original password rejection. HI review
before merge remains recommended for the changed security workflow.

## KSR4 — blank unlock dialog after relaunch

HI reported that double-clicking a saved key in locked Keymaster opens a blank
Unlock Keymaster dialog. The traceback identifies grab_set before the window is
viewable; it also runs before any form controls are built. Correct the lifecycle
to build the form, wait for visibility, then acquire the grab and set focus.
Exercise setup and unlock with delayed mapping, plus reopen/two saved keys/
double-click/unlock/apply. If that path retains a locked row snapshot after
unlock, reload the selected key before Apply. Cancellation must not apply a key
or leave an orphan dialog. No store, passphrase, schema, or cryptography change.

Validation: failure-first real Tk regression, existing Keymaster headless and
rendered tests, dialog/theme/messagebox guardrails, and setup/unlock layout audit.
Use synthetic temporary stores/configs; do not touch HI's two saved keys. A
distinct RA reviews the patch and owns the acceptance commit. Rollback is a new
revert commit, never a history rewrite. This focused follow-up is authorized by
HI's regression report within the accepted recovery/UI work.

KSR4 accepted by separate RA on 2026-10-13. The form now exists and is viewable
before the modal grab; focus remains the final initialization step. Hidden
parents do not prevent mapping, cancellation during mapping returns cleanly,
and other grab failures destroy the dialog before propagating. Apply reloads
the original selected key ID after unlock instead of using its locked snapshot;
a missing row stops without writing configuration.

Independent RA command:

```bash
xvfb-run -a ./venv/bin/python -m pytest -q \
  gui/tests/test_keymaster_security_rendered.py \
  gui/tests/test_keymaster_security.py \
  gui/tests/test_keymaster_window.py \
  gui/tests/test_dialog_audit.py \
  gui/tests/test_theme_style_guardrail.py \
  gui/tests/test_messagebox_guardrail.py
```

Output: **98 passed in 9.99s**. Regression coverage includes delayed mapping
with the reported native precondition enforced, two-key reopen/double-click/
unlock/apply, wrong password, cancellation, deletion during unlock, withdrawn
parent, and close/grab failure cleanup. DA's failure-first run reported
**3 failed, 1 passed** before the fixes.

DA command `./venv/bin/python scripts/run_dialog_audit.py --scenario
keymaster-setup,keymaster-unlock --theme both --output
/tmp/dirracuda-keymaster-visibility-audit` reported **12 results, 0 failed,
0 uncovered**. RA inspected the desktop-light unlock screenshot: the full form
and focused passphrase entry are visible. An additional DA temporary-store
diagnostic generated actual Treeview press/release pairs and reported
**PASS: real Treeview double-click binding opens a mapped modal and applies the
second saved key after unlock**, with no callback exceptions or remaining grab.

No Blocker or Required findings remain; `git diff --check` passed. Verification
used Linux/Xvfb and synthetic stores/configuration. HI's two saved keys were
not read, reset, or modified. This follow-up diagnoses the blank-dialog traceback,
not the earlier unexplained password-rejection report. Nothing was pushed.

## KSR5 — current-behavior documentation and screenshot

HI requested a README review, refreshed screenshot, and technical reference
revision. HI clarified that the manuals should describe current behavior;
incident history and dialog implementation details remain here in KSR4.
Review both manuals against the code, capture the current desktop controls with
synthetic entries in an isolated store, and retain recovery boundaries without
turning the reference into an edit history. No product changes are planned.
Validate links, screenshot readability, and behavioral accuracy; a separate RA
reviews and accepts this documentation card. Rollback uses a new revert commit.

KSR5 accepted by separate RA on 2026-10-13. README and the technical reference
describe current controls, exact-input passphrases, reset boundaries, Apply,
storage internals, and credit checks. Dialog-remediation and acceptance history
remain in this development packet. The refreshed screenshot shows the current
unlocked window with two synthetic entries and no queried credit results.

RA required one accuracy correction: queued scans do not universally retain
the key from queue creation. `dashboard_scan.launch_next_queued_scan` builds
each protocol's options at launch; without `api_key_override`, the backend
loads current effective configuration. Both manuals now distinguish running
scans from queued scans and explain run-specific overrides. No product change
was needed. This illustrates why copying a prior manual statement is not a
substitute for checking runtime behavior.

DA screenshot command:
`xvfb-run -a -s '-screen 0 1280x800x24' ./venv/bin/python
/tmp/keymaster-doc-capture.py` reported **PASS: captured current Keymaster;
two synthetic keys; no callback errors or external operations**. RA inspected
the capture harness and image. The harness used a disposable dialog-audit
environment; no live keys or configuration were accessed.

Independent RA checks with `./venv/bin/python - <<'PY'` verified README targets,
the unique technical-reference Keymaster anchor, and PNG integrity at
1000×490 pixels: **PASS: README targets, unique Keymaster anchor, PNG integrity
(1000x490)**. `git diff --check` passed. Product tests were not repeated for this
documentation-only card. No Blocker or Required findings remain.

Historical disposition retained from the prior technical reference: CodeQL
alert #23 was documented as a false positive because the flagged HMAC performs
keyed duplicate detection, not passphrase hashing. Its domain-separated key is
derived after PBKDF2 and is not persisted. This records the previous disposition;
the current remote alert state was not checked during KSR5. The manuals retain
the cryptographic architecture explanation without alert-triage history.
