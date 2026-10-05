# Concurrent Process Management — AA Findings & PA Handoff

**Status:** Decisions below were reviewed and **accepted by HI on 2026-10-05**
(including the DB-config Hard Stop, risk explicitly accepted). This is a
handoff for PA to turn into task cards. It is written to stand alone for a
fresh session or a different vendor — it assumes no shared context.

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

---

## 3. Accepted endstate (HI-approved 2026-10-05)

Three items. HI approved all three and dropped the fourth (per-share loop).

### D1 — One shared concurrency policy + raised defaults
- Introduce a single helper that resolves worker counts (hardware-aware where
  cheap to do so), replacing scattered per-stage literals.
- Raise the Dashboard batch probe default **3 → 6** and cap **8 → 16**.
- Expose the resolved values through the existing settings surface
  (`probe.batch_max_workers` etc.) so they remain user-overridable.

### D2 — Relieve the DB lock (S1) — **DB-config Hard Stop, HI risk-accepted**
- Enable **SQLite WAL mode** on the primary DB so readers no longer block the
  writer and vice versa.
- Give each worker thread its own short-lived connection and **narrow or
  remove the process-wide `connection_lock`** now that WAL makes concurrent
  read/write safe.
- HI explicitly acknowledged this touches DB configuration (a Charter Hard
  Stop) and accepted the risk during the exploratory conversation. PA should
  still state the exact migration/pragma mechanics and rollback in the card;
  no fresh HI gate is required to plan it.

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
- Server List batch probe default of 1 worker is noted but not the HI example;
  PA may fold a default bump into D1 if cheap, otherwise leave it.

---

## 5. Suggested card boundaries for PA (not a plan — PA owns planning)

A possible decomposition, offered only to save PA time:

1. **Card A — shared concurrency policy + raised defaults (D1).** Single
   module, settings wiring, no DB or network-behavior change. Lowest risk;
   good first card.
2. **Card B — WAL + per-thread connection + lock narrowing (D2).** The
   DB-config card. Needs: WAL pragma on open, per-thread connection lifecycle,
   lock removal/narrowing, and tests for concurrent read-during-write. Carries
   the accepted risk; warrants RA review by a different holder than DA.
3. **Card C — configurable pacing sleeps (D3).** Config plumbing + default
   changes; small.

Dependencies: B should land before or with A's raised cap takes real effect,
otherwise more workers simply contend harder on the old lock. C is independent.

---

## 6. Open questions for PA

- Exact hardware-aware formula for D1 (fixed bump vs. `os.cpu_count()`-derived)?
- WAL interaction with any existing multi-process access (CLI subprocess writing
  the same DB while the GUI reads) — confirm WAL's single-writer rule holds and
  the CLI/GUI boundary is covered by tests.
- Whether the Server List batch default (1) should move to the shared policy in
  Card A or stay as-is.

---

*Authored by AA during an exploratory session. Decisions in §3 are HI-accepted;
§5 card boundaries are a suggestion, not a committed plan.*
