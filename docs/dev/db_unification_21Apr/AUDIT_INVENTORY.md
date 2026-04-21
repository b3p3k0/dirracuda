# DB Unification Audit Inventory (2026-04-21)

## Goal
Make `dirracuda.db` the canonical/shareable store for analyst-visible host intelligence.

## Confirmed DB-backed today (already aligned)
- Host notes/favorite/avoid state (`host_user_flags`, `ftp_user_flags`, `http_user_flags`).
- Protocol server inventory + access summaries (`smb_servers`, `share_access`, `ftp_servers`, `ftp_access`, `http_servers`, `http_access`).
- Probe status/indicator counts/extracted/rce summaries (`host_probe_cache`, `ftp_probe_cache`, `http_probe_cache`).

## Confirmed non-DB persistence that affects shareability

### 1) Probe snapshots (high priority)
- SMB snapshot files: `~/.dirracuda/probes/<ip>.json`
  - module: `gui/utils/probe_cache.py`
- FTP snapshot files: `~/.dirracuda/ftp_probes/<ip>.json`
  - module: `gui/utils/ftp_probe_cache.py`
- HTTP snapshot files: `~/.dirracuda/http_probes/<ip>[_port].json`
  - module: `gui/utils/http_probe_cache.py`
- Dispatch/load path currently reads those files:
  - `gui/utils/probe_cache_dispatch.py`

### 2) SMB probe-status legacy fallback (high priority)
- SMB status derivation still falls back to file cache + `settings_manager` status map in server-list flow.
  - `gui/components/server_list_window/actions/batch_status.py`
  - `gui/utils/settings_manager.py` probe status map (`probe.status_by_ip`)

### 3) Snapshot-path contract in DB points to files (high priority)
- Probe cache tables currently store `snapshot_path` text, not snapshot payload JSON.
  - `shared/db_migrations.py`
  - writes via `DatabaseReader.upsert_probe_cache_for_host(...)`

### 4) Extraction summary logs written to filesystem (medium priority)
- Extraction summaries written as JSON files under `~/.dirracuda/extract_logs`.
  - `gui/utils/extract_runner.py::write_extract_log`

### 5) Experimental sidecar SQLite DBs (scope decision)
- SearXNG dork: `~/.dirracuda/se_dork.db`
  - `experimental/se_dork/store.py`
- Reddit ingest: `~/.dirracuda/reddit_od.db`
  - `experimental/redseek/store.py`
- Dorkbook: `~/.dirracuda/dorkbook.db`
  - `experimental/dorkbook/store.py`

## Additional operational filesystem artifacts (likely out of strict host-intel scope)
- Quarantine per-host `activity.log` file:
  - `shared/quarantine.py`
- RCE JSONL audit log:
  - `shared/rce_scanner/logger.py`

## Recommended migration order
1. Probe snapshots into DB payload columns; keep path compatibility as read fallback only during migration.
2. Remove SMB file/settings fallback from probe status rendering; source from DB only.
3. Move extraction summary metadata into DB (new table) and keep optional filesystem export if needed.
4. Decide whether experimental sidecar DBs should be consolidated into `dirracuda.db` now or handled in a follow-up wave.

## Guardrails
- No public GUI signature breaks.
- Backward-compatible migration: support existing `snapshot_path` rows and old file caches until backfilled.
- Deterministic cancellation/close behavior unaffected.
- Keep each card surgical; one primary target per issue.
