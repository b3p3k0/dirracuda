# TASK CARD C14 — Extraction-manifest identity and opt-in handoff

Status: **implementation complete; validation passed**

## Scope

C14 connects completed extraction flows to Analyst without guessing host identity or
rescanning unrelated files. It adds a portable structured persistence reference, an
exact manifest loader, descriptor-safe inventory of only the manifest's final saved
paths, and an opt-in post-extract offer that is off by default.

## Locked contract

- `write_extract_log()` returns `ExtractSummaryReference`, never a synthetic filename.
  A reference identifies either one exact primary-DB row id or one absolute fallback
  JSON file and exposes only a content-free display token.
- New fallback JSON uses a versioned envelope containing the exact copied host identity
  plus the extraction summary. Legacy plain-summary JSON is browseable but not eligible
  for automatic Analyst launch unless its required identity is explicit.
- Primary-DB reads inspect the real runtime table/columns and query by row id only.
  Missing table/columns, duplicate/nonfinite JSON, row/summary identity mismatch, or
  unexpected types fail closed without a sidecar mutation.
- The manifest scope is exactly the ordered, unique `saved_to` paths after ClamAV
  routing. C14 computes a canonical common directory root, walks each saved path by
  no-follow descriptors, hashes each visible regular file independently, and persists
  missing/unsafe paths as closed exclusions. It never inventories unrelated siblings.
- Analyst stores `source_mode=extraction_manifest` and the copied host type, protocol
  server id, address, port, and exact `extract_run_summaries.id`. The immutable root
  identity continues to fence later reopen operations.
- Output defaults to `<common-root>/_analyst/<safe-host-id>/`; report label and host
  identity are displayed for confirmation rather than inferred from a directory name.
- The post-extract offer is controlled by `analyst.offer_after_extract`, defaults false,
  and is unavailable in reduced isolation. Persistence completes before the UI offer.
  Decline/cancel creates no Analyst run. Accept creates and launches exactly one run.
- Every extraction entry path—single-host detail, batch, and dashboard post-scan—must
  persist a reference before offering Analyst. Test doubles returning legacy `Path`
  values remain display-compatible but are not eligible for automatic handoff.

## Acceptance

- DB row-id lookup cannot drift to a newer row for the same host/time.
- Fallback envelope round-trips exact identity; legacy/malformed/foreign JSON fails
  automatic launch without mutation.
- Saved-path duplicate, relative, NUL/backslash, symlink, mount crossing, special file,
  mutation and hash-race cases are rejected or recorded with a closed exclusion.
- Manifest inventory contains only listed final paths, including promoted/quarantined
  outputs; unrelated siblings and `_analyst` output never enter scope.
- Opt-in defaults off, persists through the Analyst config shard, prompts only after
  manifest persistence, and keeps every Tk action on the UI thread.
- No private path/body/error enters routine logs, task names, exceptions, or repr.
- No new primary schema or migration is introduced; C13 standalone launch remains
  compatible; all extraction/Analyst/GUI regressions stay green.

## Implementation outcome

- The structured primary-row/fallback reference, strict loader, recent exact-row
  selector, descriptor-safe manifest inventory and manifest-run service are complete.
- Single-host detail, Server List batch and dashboard post-scan extraction persist the
  reference before the default-off UI offer. Accepted offers use Fast/strict mode.
- Focused C14/extraction tests, the complete shared suite and the complete GUI suite
  pass; production modules compile and the whitespace guard is clean. No private
  document, external network request, primary-schema change or dependency change was
  used for acceptance.
