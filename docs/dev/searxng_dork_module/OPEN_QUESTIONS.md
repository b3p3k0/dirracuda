# SearXNG Dork Module - Decision Log

Date: 2026-04-18

## Resolved Decisions

## D1 - Search backend source

Decision:
- Use SearXNG only in v1.

## D2 - Instance management

Decision:
- Manual single-instance URL entry only.
- No searx.space auto-import in v1.

## D3 - Instance count/failover

Decision:
- Single instance only in v1.

## D4 - Default instance

Decision:
- Default URL is `http://192.168.1.20:8090`.

## D5 - Candidate verification path

Decision:
- Reuse existing HTTP probe/verification path instead of introducing a new crawler.

## D6 - Experimental tab strategy

Decision:
- Replace `placeholder` with `SearXNG Dorking` tab.

## D7 - Setup troubleshooting to document

Decision:
- Include SearXNG `search.formats` guidance.
- Explicitly document `format=json` returning 403 when json is not enabled.

## D8 - Promotion UI strategy in v1

Decision:
- Shipped "Add to dirracuda DB" as a row context-menu action in the results browser.
- Promotion is always explicit/manual (no automatic promotion).
- When `add_record_callback` is present (Server List window is open), the action fires the existing add-record path.
- When `add_record_callback` is absent, the action shows "Not available". Open the Server List once and reopen Results DB to enable the callback.

## D9 - Classification depth in v1

Decision:
- Shipped with verifier primitives only: `try_http_request` + `validate_index_page` from `commands/http/verifier.py`.
- Deeper `run_http_probe` snapshot mode deferred to v2.

## D10 - Export formats in v1

Decision:
- CSV/JSON export from the results browser deferred to v2.
- v1 results browser is read-only with row actions (Copy URL, Open in browser, Add to dirracuda DB).

