# Scan Dialog Modularization Plan

Date: 2026-04-20

## Trigger

`gui/components/scan_dialog.py` is above file-size guardrails (`2105` lines).

## Refactor Objective

Split SMB scan dialog responsibilities into focused modules while preserving behavior.

## Proposed Breakdown

1. `scan_dialog_layout.py` for widget construction/layout helpers.
2. `scan_dialog_state.py` for load/persist/coercion logic.
3. `scan_dialog_validation.py` for input and country/region validation.
4. `scan_dialog_templates.py` for template save/load/apply flows.
5. `scan_dialog_controller.py` as thin orchestrator (`ScanDialog` public surface).

## Acceptance

1. `scan_dialog_controller.py` stays under 1500 lines.
2. Existing scan dialog tests pass unchanged or with minimal fixture updates.
3. No behavior drift in scan request payloads or settings persistence keys.
