# Concurrent Process Management — AA Findings & PA Handoff

**Status:** Decisions below were reviewed and **accepted by HI on 2026-10-05**
(including the DB-config Hard Stop, risk explicitly accepted). The three
follow-on questions were also **resolved with HI on 2026-10-05** (see §6) —
this is a complete set of decisions, not open questions. This is a handoff for
PA to turn into task cards. It is written to stand alone for a fresh session or
a different vendor — it assumes no shared context.

**Role at authoring:** AA (advising). **Track:** full process.

---

## 1. Goal (from HI)

Optimize task management. Several processes were intentionally built
single-threaded and sequential in early phases ("start small, build slow").
Now optimize what exists **without overreaching**. Driving example: the
desktop GUI's **Dashboard batch probe** feels like it probes one host at a
time. The analyst workstation has high RAM, a decent CPU, and a gigabit link;
all added load lands on the workstation, and probing host B does not affect
host A. So we should probe several hosts at once.

---

## 2. What was found (facts, read from the code 2026-10-05)

### 2.1 Most of the pipeline is already concurrent

The "one at a time" intuition does **not** match most paths. Current state:

| Path | File | Concurrent? | Default / cap |
|---|---|---|---|
| CLI SMB discovery (auth probe) | `commands/discover/auth.py:131` | Yes | `discovery.max_concurrent_hosts`=10, `discovery.max_worker_cap`=20 |
| CLI SMB access (share enum) | `commands/access/operation.py:102` | Yes | `access.max_concurrent_hosts`=10 |
| CLI FTP discovery / access | `commands/ftp/operation.py:104,201` | Yes | 10 / 4 |
| CLI HTTP discovery / access | `commands/http/operation.py:114,218` | Yes | 10 / 4 |
| **GUI Dashboard batch probe** (HI's example) | `gui/components/dashboard_batch_ops.py:615` | **Yes** | `probe.batch_max_workers`=**3**, cap 8 |
| GUI Server List batch probe | `gui/components/server_list_window/actions/batch.py:72` | Yes | `worker_count` default **1**, cap 8 |
| Per-host share loop inside one probe | `gui/utils/probe_runner.py:75` | **No** (sequential, by design) | — |

**Key correction to the mental model:** the Dashboard batch probe already runs
3 hosts concurrently over the network via `ThreadPoolExecutor`. There is no
missing thread pool. What makes it *feel* serial is DB-lock contention and
injected pacing sleeps (below), plus a modest default worker count.

No `~/.dirracuda/state/gui_settings.json` exists on this machine, so the GUI
runs at hardcoded defaults (dashboard probe = 3 workers).

### 2.2 The real serial / contention spots

| # | Spot | File | Effect |
|---|---|---|---|
| S1 | **Global `connection_lock`** wraps the entire DB open→use→close in the `_get_connection` context manager | `gui/utils/database_access_core_methods.py:242-284` (lock acquired at line 256) | One process-wide lock on `DatabaseReader`. Every probe's persist **and** every dashboard-refresh read query serialize against each other. DB-only (not held across network I/O), but real contention, especially since dashboard refresh polls on the UI thread via `after(...)`. |
| S2 | Injected pacing sleeps | `conf/config.json`: `connection.rate_limit_delay`=1s, `connection.share_access_delay`=2s, `file_collection.download_delay_seconds`=2s | Per-host/share delays compound even when worker slots are free. |
| S3 | Per-share loop within a single host | `gui/utils/probe_runner.py:75` | One host's shares are probed one at a time. **OUT OF SCOPE** (see §4). |
| S4 | Conservative default worker count | `dashboard_batch_ops.py:532` (default 3, clamp 1–8) | Modest for the target hardware. |

Note on S1: `DatabaseReader.connection_lock` is created in
`gui/utils/database_access.py:61` and is the lock used inside
`_get_connection`. Connections are opened with `check_same_thread=False` and a
new `sqlite3.connect(...)` per `_get_connection` call (not a single long-lived
connection), so the serialization comes from the **lock**, not from a shared
connection object. Removing/narrowing the lock requires WAL to stay safe —
see D2.

**Journal-mode facts (verified on the live DB 2026-10-05):**
- Live `~/.dirracuda/dirracuda.db` is currently `journal_mode=delete`,
  `busy_timeout=5000`, `synchronous=2` (FULL). **WAL is NOT currently active.**
- However, the CLI backend `tools/db_manager.py:95-103` already opens
  **thread-local connections** (`self._local.connection`,
  `check_same_thread=False`) and sets `PRAGMA journal_mode=WAL` +
  `PRAGMA foreign_keys=ON`. So the exact per-thread pattern D2 needs already
  exists in-repo as a proven template; the GUI `DatabaseReader` simply does not
  use it. (The live file reading `delete` indicates WAL is not being applied on
  the primary-DB path the GUI uses, or is reverting — PA should standardize it
  so WAL sticks across every opener.)
- WAL is multi-process safe (GUI + CLI subprocess): one writer at a time plus
  concurrent readers, coordinated through the `-wal`/`-shm` sidecar files, with
  all accessors on local disk under `~/.dirracuda`. `busy_timeout` (already
  5000ms) absorbs writer contention.

---

## 3. Accepted endstate (HI-approved 2026-10-05)

Three items. HI approved all three and dropped the fourth (per-share loop).

### D1 — One shared concurrency policy + raised defaults
- Introduce a single helper that resolves the probe worker count, replacing
  scattered per-stage literals.
- **Fixed default 12, cap 16.** No `cpu_count()`-derived auto-scaling — probe
  work is network-I/O-bound, so core count is a poor proxy and auto-detect only
  adds code and test surface. (HI chose 12 over the recommended 8;
  user-overridable up to the 16 cap.)
- The **Server List** batch probe (currently defaults to 1 worker,
  `gui/components/server_list_window/actions/batch.py:72`) uses this **same
  shared default (12)** — the 1-vs-3 inconsistency is removed (resolves Q3).
- Expose the resolved values through the existing settings surface
  (`probe.batch_max_workers` etc.) so they remain user-overridable.

### D2 — Relieve the DB lock (S1) — **DB-config Hard Stop, HI risk-accepted**
- Enable **SQLite WAL mode** consistently across **every opener of the primary
  DB** so readers no longer block the writer and vice versa, and so the file
  reliably stays in WAL (the live file is currently `delete` — see §2.2).
- Switch `DatabaseReader` to **thread-local connections** following the
  existing template in `tools/db_manager.py:95-103`
  (`self._local.connection`, `check_same_thread=False`, WAL + `foreign_keys=ON`
  on first open per thread), and **drop the process-wide `connection_lock`**
  (`gui/utils/database_access_core_methods.py:256`) now that WAL + per-thread
  connections make concurrent read/write safe.
- Set `busy_timeout` explicitly on GUI connections (CLI/live already 5000ms) to
  absorb writer contention between the GUI and any CLI subprocess.
- HI explicitly acknowledged this touches DB configuration (a Charter Hard
  Stop) and accepted the risk. PA should still state the exact pragma mechanics
  and rollback in the card; no fresh HI gate is required to plan it.

### D3 — Make pacing sleeps (S2) configurable / reducible
- Surface `rate_limit_delay`, `share_access_delay`, and the probe-relevant
  `download_delay_seconds` as tunables and lower the probe-path defaults.
- Rationale (HI): the load lands only on our workstation, and probing host B
  does not affect host A, so aggressive pacing buys little here.

---

## 4. Explicitly out of scope

- **S3 / per-share-loop parallelism** — dropped by HI. Smallest payoff, most
  intrusive to `probe_runner.py`.
- **Full SQLite → schema/engine rework** — not in scope. D2 is a pragma +
  connection-strategy change only, not a schema migration.
- (Server List batch probe default is now folded into D1 — no longer deferred.)

---

## 5. Suggested card boundaries for PA (not a plan — PA owns planning)

A possible decomposition, offered only to save PA time:

1. **Card A — shared concurrency policy + raised defaults (D1).** Single
   helper module + settings wiring; default 12 / cap 16; Server List and
   Dashboard both consume it. No DB or network-behavior change. Lowest risk,
   but see the dependency note.
2. **Card B — WAL + thread-local connections + drop `connection_lock` (D2).**
   The DB-config card. Needs: WAL pragma on every primary-DB opener,
   thread-local connection lifecycle on `DatabaseReader` (template:
   `tools/db_manager.py:95-103`), removal of the global `connection_lock`,
   explicit `busy_timeout`, and tests for concurrent read-during-write plus the
   GUI/CLI-subprocess boundary. Carries the accepted risk; **RA must be a
   different holder than DA** (Charter).
3. **Card C — configurable pacing sleeps (D3).** Config plumbing + lowered
   probe-path defaults; small. Independent.

**Dependency:** land **B before A's raised worker count takes real effect** —
with the old global lock still in place, 12 workers would simply contend harder
on one lock and the persist step would bottleneck. Suggested order: B → A → C,
or merge A+B if PA prefers one DA to own both. C can go any time.

---

## 6. Resolved decisions (were open questions; settled with HI 2026-10-05)

| # | Question | Decision |
|---|---|---|
| Q1 | Worker-count formula for D1 | **Fixed default 12, cap 16, user-overridable. No `cpu_count()` auto-scaling** (I/O-bound work; auto-detect not worth the surface). |
| Q2 | WAL safety with GUI + CLI-subprocess multi-process access | **Safe — standardize it.** WAL allows one writer + concurrent readers across processes on local disk; `busy_timeout` (5000ms) absorbs contention. Pattern already proven in `tools/db_manager.py`. PA must include a test for the GUI/CLI-subprocess write boundary. |
| Q3 | Server List batch default (1 worker) | **Fold into the shared policy (Card A) at the same default (12).** |

These are decisions, not suggestions. The only item PA should still exercise
judgment on is the **card ordering / A+B merge** in §5, and the concrete test
matrix for Card B.

---

*Authored by AA. Decisions in §3 and §6 are HI-accepted (2026-10-05). §5 card
boundaries and ordering are a recommendation for PA to finalize.*
