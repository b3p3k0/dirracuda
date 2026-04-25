# Config Save Investigation Notes (2026-04-25)

## Reported Symptom
- Fresh install with no `shodan.api_key`.
- Start scan prompts for key.
- Scan runs using entered key.
- Key does not remain persisted in `conf/config.json`.

## Reproduction
1. Load `XSMBSeekConfig` from `dirracuda` (captures in-memory config snapshot).
2. Write `shodan.api_key` directly to config file (simulating dashboard prompt save).
3. Call `XSMBSeekConfig.save_config()`.
4. Observe `shodan.api_key` reverted to prior in-memory value (empty string).

## Root Cause
- `XSMBSeekConfig.save_config()` wrote the entire in-memory config snapshot back to disk.
- Other runtime config writers (dashboard prompt, dork editor, etc.) update file directly.
- Later `save_config()` call clobbered those external updates with stale in-memory data.

## Fix Strategy
- Keep `XSMBSeekConfig` as owner of managed fields only:
  - `gui_app`
  - `database.path`
- On `save_config()`:
  1. Read latest on-disk config.
  2. Merge managed overlay from current in-memory state.
  3. Write merged result.
- Preserve non-managed keys from disk (`shodan.*`, protocol dorks, etc.) to prevent regression.

## Guardrails Added
- Managed overlay merge helpers in `XSMBSeekConfig`.
- Regression tests for:
  - preserving externally updated `shodan.api_key`
  - applying managed updates without clobbering unrelated keys.

## Follow-up Suggestions
- Continue converging direct config JSON writes into a shared config mutation layer.
- Explicitly document config ownership boundaries:
  - `gui_app`/path sync manager
  - scan/runtime feature writers
  - config editor workflows
