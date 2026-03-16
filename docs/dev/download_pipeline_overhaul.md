# Download Pipeline Overhaul (Phase 1)

Goal: reduce perceived “dead air” before downloads start in File Explorer by overlapping directory enumeration with downloads.

Scope (code)
- `gui/components/file_browser_window.py`: orchestrate producer/consumer, status updates, limits enforcement.
- Reuse `SMBNavigator.download_file` (already supports progress_callback).

Design
1) Producer/consumer queue
   - Producer thread walks selected folders (was `_expand_directories`), enqueues file tuples `(path, mtime, size)`.
   - Bounded queue (e.g., max 200 items) to cap memory; producer blocks when full.
   - Download worker consumes immediately; UI shows progress on first item.

2) Limits while streaming
   - Track cumulative `files_enqueued` and `bytes_enqueued` in producer; respect `max_files`, `max_total_mb`, `max_file_mb` as we enqueue.
   - Skip items that violate per-file limit, stop enqueuing when totals exceeded, record skip/errors like today.

3) Status & UX
   - Status heartbeat during enumeration: “Enumerating… {count} queued”.
   - Download status per file via progress_callback (already in place) + file counter “Downloading {n}/{total_found_so_far}”.
   - When producer finishes, worker continues until queue empty; then show summary as today.

4) Cancellation & shutdown
   - One shared cancel event; producer and consumer both check it.
   - On window close or user cancel, set event, drain queue safely, and close dialog gracefully.

5) Fallbacks
   - If only files (no folders), skip producer and push the file list directly to the queue to avoid extra thread.
   - If total_shares/files == 0, short-circuit with a friendly message.

Risks / mitigations
- Concurrency races: use thread-safe Queue, keep UI updates via `_safe_after`.
- Memory blow-up: bounded queue, stop enqueuing when limits hit.
- Lost errors: accumulate `expand_errors`/`download_errors` as today; surface top N in summary.

Test outline (manual)
- Files only: multiple small files; expect immediate download start.
- Folder with many files: see “Enumerating…” then downloads start before enumeration ends.
- Limits: set small `max_files`/`max_total_mb`; verify enqueue stops and summary reports skipped.
- Cancel mid-run: ensure both threads stop and UI returns to idle.

---

## Phase 2 (proposed): Parallel downloads with size-aware throttle

Goal: allow a small worker pool to pull from the download queue so many small files finish faster, while keeping large files serialized to avoid hammering SMB servers.

Concept
- Single producer (existing) feeds two bounded queues: `small_q` (default path) and `large_q` (files > LARGE_MB).
- `small_q` is drained by N workers (default 2). `large_q` is drained by a single worker.
- Shared cancel event stops all workers; shared counters keep UI accurate.

Config knobs (proposed)
- `file_browser.download.worker_count` (int, default 2, clamp 1–3)
- `file_browser.download.large_file_mb` (int, default 25) — size above which files go to the single large-file lane
- Optional: `file_browser.download.queue_max` (int, default 200) if we want it tunable; otherwise keep current bound.

Status/UX
- Keep one status line; show worker hint, e.g., `Downloading X/Y files (2 workers)...`.
- Per-file progress still shown for the file the last worker reported (good enough for now).

Risks / mitigations
- SMB throttling: cap worker_count at 3, default 2; consider backing off on repeated timeouts (future).
- Races on counters: guard completed/total with a lock or use atomic-like updates; route UI updates through `_safe_after`.
- Progress spam: keep 200ms throttle per worker callback.

Implementation steps
1) Config: add defaults in `file_browser` section (`worker_count`, `large_file_mb`).
2) Producer: when enqueueing, route to `large_q` if size > threshold else `small_q`; both bounded.
3) Consumers: spawn `worker_count` threads on `small_q`, 1 on `large_q`. All share cancel event and write to shared counters.
4) Status: include worker count in initial status; keep per-file progress callback, throttled.
5) Summary/errors: merge results from both queues; preserve current messaging.
6) Tests: manual — many small files (expect faster completions); one large + many small (large goes to solo lane, smalls continue). Cancel mid-run.

ASCII UI sketch (status line)
```
[ Downloading 7/42 files (2 workers) ]  \\server\\share\\path\\file.txt  3.1 MB
```

Where to expose settings
- Keep defaults in `conf/config.json` under `file_browser.download`.
- Add UI inputs in File Browser settings/preflight: two small controls for “Download workers [1–3]” and “Large file threshold (MB)”. Make them part of the first pass so operators can tune without editing JSON.
