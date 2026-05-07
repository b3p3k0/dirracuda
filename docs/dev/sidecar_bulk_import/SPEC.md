# SPEC - Sidecar Bulk Import (SearXNG + Reddit)

## Contract
- New helper: `promote_sidecar_prefills(db_reader, prefills, *, cancel_event=None, progress_callback=None) -> dict`
- Summary keys: `selected`, `processed`, `inserted`, `updated`, `skipped`, `failed`, `cancelled`, plus sampled reason lists.
- Single-row promotion API remains unchanged.

## UI Behavior
- `Add to dirracuda DB`:
  - Single selection: existing behavior.
  - Multi-selection: background bulk flow with `BatchStatusDialog` progress + cancel.
- Bulk fallback policy:
  - If direct bulk callback is unavailable, show one informative message and do not loop legacy add dialog.

## Wiring
- Dashboard experimental opener passes both:
  - `promote_record_callback` (single)
  - `promote_records_callback` (bulk)
- Browser windows accept optional `promote_records_callback`.
