# Tmpfs Quarantine v1 - Architecture

Date: 2026-03-28  
Status: Draft implementation architecture

## Goal

Add optional in-memory quarantine storage for Dirracuda quarantine writes, while preserving current disk behavior when disabled or unavailable.

## Runtime Lifecycle

1. Startup bootstrap
- Entry points (`dirracuda`, `gui/main.py`) call `bootstrap_tmpfs_quarantine(config_path=...)` once.
- Config determines:
  - `quarantine.use_tmpfs` (default `false`)
  - `quarantine.tmpfs_size_mb` (default `512`, bounded)
- If enabled and Linux:
  - Check mountpoint `~/.dirracuda/quarantine_tmpfs`.
  - If already mounted as tmpfs, use it.
  - If not mounted, attempt `mount -t tmpfs tmpfs -o size=<N>M[,noswap] <mountpoint>`.
- If any failure occurs, runtime falls back to disk quarantine root and publishes a one-time warning payload.

2. Runtime path resolution
- All quarantine path creation flows route through `shared/quarantine.py`.
- `shared/quarantine.py` resolves the effective root via `shared/tmpfs_quarantine.py`.
- Result:
  - tmpfs active -> writes go to `~/.dirracuda/quarantine_tmpfs/...`
  - tmpfs disabled/unavailable -> writes go to existing disk quarantine roots.

3. Shutdown gate + cleanup
- On app close:
  - If tmpfs is active and contains quarantine artifacts, show destructive confirmation dialog.
  - Cancel -> abort close.
  - Confirm -> cleanup tmpfs contents.
  - Unmount only if the current app process mounted tmpfs.

## Components and Responsibilities

1. `shared/tmpfs_quarantine.py` (new)
- Owns tmpfs runtime state and policy.
- Handles platform gating, mount state checks, mount/umount calls, warning payload, effective-root resolution, and cleanup helpers.

2. `shared/quarantine.py`
- Continues to provide quarantine directory builders.
- Delegates root selection to tmpfs runtime manager.

3. `gui/components/app_config_dialog.py`
- Adds tmpfs controls:
  - `Use memory (tmpfs) for quarantine`
  - `Max size (MB)`
- Disables quarantine directory chooser while tmpfs checkbox is enabled.
- On non-Linux, shows tmpfs controls as disabled with explanatory text.

4. GUI entrypoints (`dirracuda`, `gui/main.py`)
- Bootstrap tmpfs runtime at startup.
- Display one-time fallback warning modal when provided.
- Apply close-time destructive warning and cleanup logic.

## Config Contract

```json
"quarantine": {
  "use_tmpfs": false,
  "tmpfs_size_mb": 512
}
```

Notes:
- `tmpfs_size_mb` range for v1: `64-4096`.
- Disk quarantine keys remain canonical fallback roots:
  - `file_browser.quarantine_root`
  - `ftp_browser.quarantine_base`
  - `http_browser.quarantine_base`
  - `file_collection.quarantine_base`

## Failure/Fallback Model

1. Unsupported platform
- If `use_tmpfs=true` on non-Linux, fallback to disk with one-time warning.

2. Mountpoint conflict
- If mountpoint exists but is non-tmpfs mount, fallback to disk with warning.

3. Mount failure
- No privilege escalation.
- Fallback to disk with warning and continue runtime.

4. Cleanup failure
- Do not crash close flow.
- Log warning and continue shutdown.

## Out of Scope (v1)

1. User-configurable tmpfs mountpoint
2. Automatic privilege escalation (`sudo`)
3. Runtime hot-reload guarantees for tmpfs toggles without restart
