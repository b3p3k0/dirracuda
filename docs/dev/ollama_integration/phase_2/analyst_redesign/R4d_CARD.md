# R4d — Advanced model dropdown wiring (connect / persist / select / use)

- Date: 2026-09-18
- Type: GUI code card. codex implements, Claude validates (xvfb).
- Depends on: R4b (service.discover_models / list_discovered_models + analyst_discovered_model),
  R4c (run creation accepts a model_tag+digest pair). UI_CONTRACT.md section 2; Amendment A2.
- Operator UX: "connect once to pull the model list; then select from menu; persist the list;
  refresh if you change your backend."

## Goal

Turn the Advanced dialog's static Model label into a real dropdown backed by the persisted
discovered-model list, with an explicit Connect/Refresh, and make the chosen model actually drive
the run.

## Deliverables — `gui/components/experimental_features/analyst_tab.py`

1. **Advanced dialog Model section**: replace the static qualified-model label with:
   - a `ttk.Combobox` (readonly) listing `service.list_discovered_models()` as `model_tag`
     (keep each row's digest alongside for selection); pre-select the persisted choice if present.
   - helper text: "Connect once to pull the model list from the server, then pick one. Refresh if
     you change your backend." (exact-ish; keep it short).
   - a **Connect / Refresh** button that calls `service.discover_models()` OFF the Tk thread; on
     success repopulate the combobox and post a short status on the Tk thread; on failure show a
     `safe_messagebox` "Could not reach the model server on loopback." (content-free). The button
     is the ONLY thing that contacts the server; opening the dialog does NOT (lesson 161).
   - if the list is empty and nothing is persisted, show the pinned default and a hint to Connect.
2. **Persist the selection** to the experimental settings shard (a new key, e.g.
   `analyst.selected_model_tag` + `analyst.selected_model_digest`), saved on dialog Save.
3. **Use it at run creation**: Analyze passes the selected (model_tag, model_digest) into
   `service.create_and_launch` / `create_manifest_and_launch` (which forward to
   create_directory_run/create_manifest_run per R4c). If nothing is selected, pass none (defaults
   to the pinned model). Do not contact the server at Analyze time; the digest comes from the
   persisted discovered-model row.

## Tests — `gui/tests/test_analyst_r4d.py` (MagicMock / withdrawn root)
- Opening Advanced makes NO discover_models call (no server contact on open).
- The combobox lists models from list_discovered_models and pre-selects the persisted tag.
- Connect calls discover_models off-thread and repopulates the combobox; a discover failure shows
  a safe_messagebox and leaves the prior list.
- Save persists the selected tag+digest to settings.
- Analyze forwards the selected (model_tag, model_digest) to create_and_launch; with no selection
  it forwards none (default path).

## Constraints
- No server contact on dialog open or at Analyze (only the explicit Connect button). safe_messagebox,
  ensure_dialog_focus, named SMBSeekTheme styles, no worker-thread widget teardown.
- Only analyst_tab.py and the new test (+ a tiny additive settings default if needed). Do NOT
  change the worker/finalize/schema/ollama backend. File < 1700 lines.

## Acceptance (Claude validates)
1. `xvfb-run -a ./venv/bin/python -m pytest gui/tests/test_analyst_r4d.py -q` passes.
2. `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` => 0 failed (guardrails green).
3. `./venv/bin/python -m pytest shared/tests -k analyst -q` => 0 failed.
4. A manual xvfb smoke: open Advanced (no contact), the Model combobox lists persisted models.
