# U1 — Input/Output dirs on the main Analyst dialog

- Type: small GUI card. codex implements, Claude validates (xvfb).

## Change (gui/components/experimental_features/analyst_tab.py)
- Rename the main-form "Folder" label to **Input Dir** (the source folder field; keep its Browse +
  name auto-fill behavior).
- Move the **Output Dir** field from the Advanced dialog onto the MAIN form, directly beneath
  Input Dir (its own labeled path row + Browse). Keep the S-B behavior: default to
  get_paths().analyst_reports_dir, load persisted analyst.output_folder on build, save on
  Save/Analyze.
- Remove the Output folder control from the Advanced dialog (Advanced keeps Source/model-server/
  model/offer). Ensure no dangling references to the old Advanced output widget.

## Constraints
- safe_messagebox, ensure_dialog_focus, named SMBSeekTheme styles only. Only analyst_tab.py + tests.
  Do NOT change backend/worker/schema. File < 1700 lines.

## Acceptance (Claude validates)
1. Update/extend the R6a/S-B GUI tests: the MAIN form now exposes Input Dir + Output Dir (and
   Read/Analyze/Advanced); Advanced no longer has Output folder; output still defaults to
   analyst_reports_dir and persists across a rebuild.
2. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k "analyst or messagebox_guardrail or theme_style_guardrail" -q` => 0 failed.
