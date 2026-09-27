# Reddit ingestion working notes

## Current baseline

The numbered V1–V3 plans in this folder describe earlier implementation stages.
For current behavior, start with [README](../../../README.md#reddit-ingestion-redseek)
and the [integration lessons](../inetgrate_exp_feat/LESSONS_LEARNED.md), especially
C10 and C10.1: new runs use anonymous Atom/RSS feed/search and the primary DB.
User mode and unauthenticated JSON ingestion are retired. Legacy sidecar data
remains available for browsing and manual promotion.

Desktop entrypoint: `./dirracuda`. Headless service manager: `./dirracuda-d`.
Tests use `./venv/bin/python -m pytest`; targeted syntax checks use `py_compile`.
No standalone lint configuration was found during this task.

## 2026-09-27 — Attribution in server Notes

Status: implemented; automated validation PASS; HI reported “looks great.”
No commit or push performed at the initial handoff.

Request: populate editable server Notes with the Reddit username and full
original post title. HI confirmed that distinct attributions should append once
while preserving existing user notes.

Root cause: the RSS client and `reddit_posts` already retained author/title,
but the auto-sync query omitted those fields. The shared mapper and promotion
payload did not carry attribution into the protocol-specific user-flags Notes.
`reddit_targets.notes` is a separate, truncated title/body preview.

Changes:
- Join stored post metadata into current-run target reads.
- Format attribution in the shared Reddit mapper, used by auto-sync and legacy
  manual promotion: “Posted to r/OpenDirectories by u/” + username +
  “ original title: ” + the full stored title.
- Carry an optional `_append_notes` value through promotion and append within
  the server upsert transaction. A note failure rolls back that server write.
- Deduplicate complete attribution text, preserve existing Notes/favorite/avoid,
  and route HTTP notes using the resolved endpoint row ID.
- Normalize existing username prefixes; use `[unknown]` for absent/invalid
  authors and preserve `[deleted]`. Skip attribution when no usable title exists.
- Check destination Notes columns at runtime. Missing Notes storage is skipped;
  no schema changes are made. Existing Reddit schema checks remain authoritative.
- README reviewed and updated. No new Reddit requests or UI rendering work.

Scope: applies when a target is synced/promoted, including existing hosts seen
again. It does not bulk-backfill historical hosts. Deduplication uses the complete
attribution text; posts with identical author/title share one note. Notes remain
editable, so removing attribution allows a later ingest to append it again.

Validation from the repository root:

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_attribution.py -q --tb=short
```

Before the production fix: **7 failed**, confirming that successful server
insertion left Notes empty, including mocked Atom feed/new, feed/top, and search.

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_attribution.py shared/tests/test_redseek_client.py shared/tests/test_redseek_service.py shared/tests/test_redseek_store.py shared/tests/test_redseek_mapper.py shared/tests/test_redseek_main_db_sync.py shared/tests/test_se_dork_main_db_sync.py gui/tests/test_sidecar_promotion.py gui/tests/test_database_access_protocol_writes.py gui/tests/test_reddit_browser_window.py gui/tests/test_server_list_card4.py -q
```

After the fix: **301 passed**. Coverage includes long Unicode titles, escaped
Atom text, missing/deleted authors, repeat ingestion, preserving user flags and
notes, multiple HTTP ports, legacy Notes schemas, rollback, and shared promotion
regression. All test data uses temporary databases and mocked network I/O.

```bash
./venv/bin/python -m py_compile experimental/redseek/store.py experimental/redseek/mapper.py gui/utils/sidecar_promotion.py gui/utils/database_access_write_methods.py shared/tests/test_redseek_attribution.py
git diff --check
```

Both checks: **PASS**. Full-suite and live GUI/network validation were not run.

HI check: launch `./dirracuda`, run a Reddit ingest, and open an ingested host's
details from Server List. Check the username/full title in Notes. Add your own
line, close the details window, repeat the same ingest, refresh Server List, and
reopen details. Your line should remain and attribution should appear only once.
The repeated feed must include that target for the repeat-ingest check to apply.

File sizes (before → after, lines):

| File | Lines | Rubric |
|---|---:|---|
| `experimental/redseek/store.py` | 464 → 462 | Excellent |
| `experimental/redseek/mapper.py` | 143 → 157 | Excellent |
| `gui/utils/sidecar_promotion.py` | 408 → 411 | Excellent |
| `gui/utils/database_access_write_methods.py` | 1522 → 1553 | Acceptable |
| `shared/tests/test_redseek_attribution.py` | new → 175 | Excellent |
| `README.md` | 775 → 781 | Excellent |

## 2026-09-27 — Add posting date

Status: implemented; automated validation PASS; HI reported “looks good.”
HI subsequently requested a combined attribution/date commit. A commit reminder
was given before starting this follow-up.

The posting timestamp already exists in `reddit_posts.post_created_utc`, but the
sync read and attribution formatter omitted it. The sync query now includes it;
the shared mapper adds `on YYYY-MM-DD (UTC)` between username and original title.
Legacy browser queries already include this timestamp.

Use timezone-aware conversion, as described in the
[Python datetime docs](https://docs.python.org/3.13/library/datetime.html#datetime.datetime.fromtimestamp).
Numeric timestamps and numeric strings are accepted. Missing, boolean, malformed,
non-finite, and out-of-range values retain the undated wording instead of dropping
the target. Epoch zero remains a valid timestamp.

Existing undated notes are preserved; re-ingestion appends the dated version
once. No history rewrite or bulk backfill. The posting date now participates in
attribution deduplication. README reviewed and updated.

Validation:

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_attribution.py -q --tb=short
```

Before the date implementation: **14 failed, 23 passed**, confirming the missing
date while retaining undated fallback behavior.

```bash
./venv/bin/python -m pytest shared/tests/test_redseek_attribution.py shared/tests/test_redseek_mapper.py shared/tests/test_redseek_store.py shared/tests/test_redseek_main_db_sync.py gui/tests/test_reddit_browser_window.py -q
./venv/bin/python -m py_compile experimental/redseek/mapper.py experimental/redseek/store.py shared/tests/test_redseek_attribution.py
git diff --check
```

After the fix: **141 passed**. Includes UTC midnight, invalid timestamps, full
title preservation, all three protocol destinations, and repeat ingestion.
Compile and diff checks: **PASS**. No live network or GUI checks run by the agent.

HI check: run an ingest through `./dirracuda`, refresh Server List, and open a
result's details. Notes should include the posting date labelled UTC. Repeating
the ingest should not add another copy of the dated attribution.

File sizes for this follow-up (before → after): mapper 157 → 170;
store 462 → 462; attribution tests 175 → 207; README 781 → 782.
All are in the excellent range; working notes remain well below 1,200 lines.

Commit closeout: reran the full 11-file validation command recorded under the
first task against the current development branch: **316 passed**. Reviewed
README and the complete diff; only attribution/date files are included.

## Lessons to carry forward

- Server Notes live in `host_user_flags`, `ftp_user_flags`, and `http_user_flags`.
  Updating a provider's preview field alone will not populate the editable box.
- Keep Reddit mapping shared across live sync and legacy promotion.
- Preserve the full `reddit_posts.post_title`; the target preview is deliberately
  truncated and is unsuitable as an original-title source.
- Merge notes while the server write transaction holds its lock. Do not load
  cached GUI rows and later overwrite Notes with a stale copy.
- Match complete note blocks when deduplicating. Substring matching can discard
  a distinct post whose title is a prefix of another title.
- Reuse existing runtime schema guards. `open_connection` already rejects
  malformed Reddit schemas; a second fallback for those schemas is unreachable
  in normal sync. Destination user-flags tables still need their own guards.
- A display-only metadata conversion failure must not cause the shared mapper
  to discard an otherwise usable target. Keep date fallbacks local to formatting.
