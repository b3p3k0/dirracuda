# C2 — Safe inventory and worker reattachment contracts

Date: 2026-08-14
Status: **Complete**

## Issue

Later parser and orchestration cards need a deterministic inventory that never follows a
hostile path, plus a process identity strong enough to distinguish a live Analyst worker
from PID reuse after a crash or reboot. Neither contract exists in production yet.

## Root cause

Path strings, pre-open `stat()` results and PIDs are names, not durable identities. A
source can be swapped between check and open, an inode can change while it is read, and a
PID can be reused. mergerfs adds an important constraint: inode values are not guaranteed
to uniquely identify logical files, so cross-path inode deduplication would silently
collapse distinct documents.

## Scope

### Descriptor-safe inventory

- Open the selected root and every descendant relative to already-open directory file
  descriptors with `O_NOFOLLOW`, `O_CLOEXEC` and type-appropriate flags.
- Sort entry names for deterministic traversal; never use a discovered joined pathname
  for a later open.
- Inventory each regular-file path independently, stream SHA-256 from its exact open file
  descriptor, and compare pre/post descriptor metadata. A changed source is excluded with
  a stable content-free reason.
- Never infer hard-link or duplicate identity from `(st_dev, st_ino)`. This preserves
  mergerfs compatibility at the small cost of hashing two names separately.
- Do not cross a nested mount/device boundary. The selected mergerfs mount itself is
  supported because its visible tree shares the selected root device.
- Exclude symlinks, special files and every `_analyst` subtree explicitly. Preserve
  content-free exclusion counts alongside discovered regular files.
- Bound directory depth and total entries. Limit failure returns no partial inventory.
- Check caller-owned cancellation between entries and hash chunks; cancellation returns no
  partial inventory and closes every descriptor.

### Lease/reattachment evidence

- Bind a worker to PID + `/proc/<pid>/stat` field 22 start ticks + kernel boot UUID.
- Persisted heartbeat time uses the monotonic clock and is meaningful only with the same
  boot UUID.
- Pure reconciliation outcomes are: reattach to an exact live/fresh worker; clear a
  missing or identity-mismatched stale lease; or block when the exact worker is still
  alive but its heartbeat is stale/invalid.
- Never clear an exact still-live stale worker automatically: doing so could launch a
  second GPU owner if the first process resumes. A later worker/service card must pin and
  stop or otherwise resolve that process before clearing the lease.

## Out of scope

- SQLite schema, persistent atomic lease claims and sidecar paths (C8).
- Worker launch, signaling, heartbeat writes and GUI hydration (C8/C13).
- Parser selection, sandboxing or document extraction (C3+).
- Extraction-manifest handoff (C14).
- Private documents, Ollama calls, dependency files, migrations, auth and CI.

## Acceptance

1. Empty/nested trees inventory deterministically and hash exact file bytes.
2. Symlinked roots, files and directories are never followed; FIFOs/devices are never
   opened as ordinary input; `_analyst` is excluded.
3. Name swaps, post-open mutation, nested mounts, depth overflow and entry overflow fail
   closed with stable outcomes and no leaked descriptors.
4. Cancellation interrupts a multi-chunk hash promptly and leaks no descriptor.
5. Two paths reporting the same inode are still hashed/read independently.
6. Real mergerfs smoke validation passes under the canonical user-data tree.
7. `/proc` parsing handles spaces and `)` in process names, rejects malformed/bounded
   input, and matches the current process.
8. Reattachment tests cover fresh, dead, PID-reused, rebooted, future-heartbeat and
   stale-but-live states.
9. Focused tests, compile/diff checks, privacy scan and file-size checks pass; all
   production files remain below 1200 lines.
10. Root `README.md` is reviewed and remains unchanged unless user-facing behavior exists.

## Sources

- Python descriptor-relative filesystem APIs:
  https://docs.python.org/3/library/os.html
- Linux `/proc/<pid>/stat` start-time identity:
  https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
- Linux boot UUID behavior:
  https://www.kernel.org/doc/html/v6.1/admin-guide/sysctl/kernel.html
- mergerfs inode and device limitations:
  https://trapexit.github.io/mergerfs/2.42.0/faq/technical_behavior_and_limitations/
