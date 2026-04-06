# Card C2 — Runtime Behavior Parity (FTP full, HTTP worker-only)

## Context

C1 added the tuning strip UI (worker count, large-file MB) and persistence. `self.download_workers` and `self.download_large_mb` are loaded from settings at init time but are never read during downloads — both `_download_thread_fn` implementations are still simple sequential loops. C2 wires those values into the actual runtime download behavior.

**Scope:** surgical changes to the two `_download_thread_fn` methods and their tests. SMB untouched. No docs. No commit.

---

## What needs to change

### 1. `FtpBrowserWindow._download_thread_fn` (lines 918–1031)

Replace the sequential `for remote_path, file_size in file_list` loop with a multi-worker queue-routing implementation modeled on SMB's producer/consumer pattern.

**Snapshot tuning (SMB-style, prefer live UI vars with fallback):**
```python
try:
    worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
    large_threshold_bytes = max(1, int(self.large_mb_var.get() or self.download_large_mb)) * 1024 * 1024
except Exception:
    worker_count = max(1, min(3, self.download_workers))
    large_threshold_bytes = max(1, self.download_large_mb) * 1024 * 1024
```

**Queue setup:**
- Create `q_small: queue.Queue` and `q_large: queue.Queue`.
- Pre-enqueue all items from `file_list` before starting workers:
  - `file_size and file_size > large_threshold_bytes` → `q_large`
  - else → `q_small`

**`consumer(target_q)` inner function:**
- Claim first item atomically before paying connection cost (avoids `empty()` race):
  ```python
  if self._cancel_event.is_set():
      return
  try:
      first_item = target_q.get_nowait()
  except queue.Empty:
      return   # Nothing to do; skip FTP connection entirely
  ```
- `from shared.ftp_browser import FtpNavigator, FtpCancelledError, FtpFileTooLargeError`
- Create `FtpNavigator(...)` with same kwargs as `_list_thread_fn`. Set `nav._cancel_event = self._cancel_event`.
- `nav.connect(self.ip_address, self.port)` — on failure, record `first_item` path as an error in `errors` list (with `clamav_lock`), then return. Do NOT silently drop it.
  ```python
  try:
      nav.connect(self.ip_address, self.port)
  except Exception as exc:
      with clamav_lock:
          errors.append((first_item[0], f"connect failed: {exc}"))
      return
  ```
- `try / finally: nav.disconnect()`.
- Process `first_item`, then `while not self._cancel_event.is_set()` loop draining `target_q.get_nowait()`. Break on `queue.Empty`.
- Per-file: download → log → optional ClamAV post-process. All `clamav_accum` and `success_count_ref[0]` mutations under `clamav_lock`.
- Exception handling identical to current sequential code: `FtpCancelledError` → break, `FtpFileTooLargeError / FileExistsError / Exception` → status message.

**Thread launch:**
```python
consumer_threads = []
for _ in range(worker_count):
    consumer_threads.append(threading.Thread(target=consumer, args=(q_small,), daemon=True))
consumer_threads.append(threading.Thread(target=consumer, args=(q_large,), daemon=True))
for t in consumer_threads: t.start()
for t in consumer_threads: t.join()
```

**Completion:** call `self._on_download_done(success_count_ref[0], len(file_list), str(quarantine_dir), clamav_accum)`.

**`errors` list:** add `errors: List = []` (shared, mutations under `clamav_lock`) to accumulate connect-failure items. Each entry is `(path, reason_str)`. Tally in `len(file_list)` total; log to status on completion if non-empty.

**Thread safety:** `clamav_accum` and `success_count_ref[0]` mutations under `clamav_lock`. `success_count_ref = [0]` (mutable list closure).

**Cancel:** `_on_cancel` sets `self._cancel_event`. Worker navs each hold `nav._cancel_event = self._cancel_event`, so in-progress transfers raise `FtpCancelledError`. Workers check event at loop top and on first-item claim.

### 2. `HttpBrowserWindow._download_thread_fn` (lines 1401–1511)

Replace sequential loop with multi-worker single-queue implementation (NO large-file split).

**Snapshot tuning:**
```python
try:
    worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
except Exception:
    worker_count = max(1, min(3, self.download_workers))
```

**Queue setup:** ONE `q: queue.Queue`, all files enqueued. No size routing. `large_mb_var` / `download_large_mb` not read.

**`consumer()` inner function (no queue arg — closure):**
- Same first-item-claim pattern: try `q.get_nowait()` before any work; return on `queue.Empty`.
- Uses `self._navigator` (shared; stateless/per-request, safe for concurrent use).
- Same exception handling as current code (HttpCancelledError, HttpFileTooLargeError, FileExistsError).
- Accum + count updates under `clamav_lock`.

**Thread launch:** `worker_count` threads all targeting `consumer()`. Join all, then `_on_download_done`.

**No large-file routing:** enforced by single-queue design — no q_large, threshold not read.

### 3. Comments in both `__init__` methods

Lines 622 (FTP) and 1147 (HTTP): remove `(UI only in C1; C2 wires runtime behavior)` from the comment.

---

## Test changes

### `gui/tests/test_browser_clamav.py`

**Update `_make_ftp_window`:**
- Add `win.port = 21`, `win.download_workers = 1`, `win.download_large_mb = 25`.
- Add `win.workers_var = _Var(1)`, `win.large_mb_var = _Var(25)` (using existing `_Var` stub).
- Expand config: use `_load_ftp_browser_config(None)` as base, then override `quarantine_base` and `clamav`.

**Update `_make_http_window`:**
- Add `win.download_workers = 1`, `win.download_large_mb = 25`.
- Add `win.workers_var = _Var(1)`, `win.large_mb_var = _Var(25)`.
- Expand config: use `_load_http_browser_config(None)` as base, then override `quarantine_base` and `clamav`.

**Update these three FTP tests** (mock `win._navigator.download_file` no longer reached — C2 creates per-worker FtpNavigators):
- `test_ftp_download_thread_calls_postprocessor`
- `test_ftp_download_thread_disabled_no_accum`
- `test_ftp_download_pp_exception_logged_to_accum_and_quarantine`

Each needs:
```python
mock_nav = MagicMock()
mock_nav.download_file.return_value = SimpleNamespace(saved_path=saved)
with patch("shared.ftp_browser.FtpNavigator", return_value=mock_nav), \
     patch("gui.components.unified_browser_window.threading.Thread", _ImmediateThread), \
     ...:
    win._download_thread_fn([("/a.txt", 10)])
```

**HTTP tests** (`test_http_download_thread_calls_postprocessor`, `test_http_download_thread_init_error_surfaces_to_status`) remain valid — HTTP consumer still uses `self._navigator`. With `download_workers=1`, the real consumer thread starts, uses `win._navigator` (MagicMock), and join() ensures completion before assertions.

**`test_ftp_download_thread_init_error_surfaces_to_status`:** `file_list=[]` → both queues empty → first-item claim fails immediately → no FtpNavigator created. No FtpNavigator patch needed for this test.

### `gui/tests/test_ftp_browser_window.py` and `test_http_browser_window.py`

**`_make_ftp_with_settings` and `_make_http_with_settings`:** Add `load_probe_result_for_host` patch for hermeticity.

`load_probe_result_for_host` is imported inside `__init__` via a function-local `from gui.utils.probe_cache_dispatch import load_probe_result_for_host`. This means patching the source module (`gui.utils.probe_cache_dispatch.load_probe_result_for_host`) works correctly only if the patch is **active at the moment the constructor runs**. The existing `with patch.multiple(...)` context already wraps the constructor call — add the patch to that same `with` block to guarantee timing:

```python
with patch.multiple(
    "gui.components.unified_browser_window.FtpBrowserWindow",
    _build_window=MagicMock(),
    _navigate_to=MagicMock(),
    _run_probe_background=MagicMock(),
    _apply_probe_snapshot=MagicMock(),
), patch("gui.components.unified_browser_window.threading.Thread", _NoopThread), \
   patch("gui.utils.probe_cache_dispatch.load_probe_result_for_host", return_value=None):
    win = FtpBrowserWindow(parent=MagicMock(), ip_address="1.2.3.4", settings_manager=sm)
```

Same pattern for `_make_http_with_settings`.

### New `TestC2RuntimeBehavior` class in `test_browser_clamav.py`

**Helper — `_NoStartThread` (captures threads without running them):**
```python
class _NoStartThread:
    _instances: list = []
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        _NoStartThread._instances.append(self)
    def start(self): pass
    def join(self): pass
```

Every test using `_NoStartThread` must reset `_instances` at the top:
```python
_NoStartThread._instances.clear()
```
This applies to **all 4 tests** to prevent inter-test leakage.

---

**Test 1 — FTP worker count starts correct thread count:**

`download_workers=2` → `worker_count=2` → 2 small workers + 1 large worker = 3 threads.

```python
_NoStartThread._instances.clear()
win = _make_ftp_window(tmp_path)
win.download_workers = 2
win.workers_var = _Var(2)
with patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread), \
     patch("shared.ftp_browser.FtpNavigator"):   # not called but imported
    win._download_thread_fn([])
assert len(_NoStartThread._instances) == 3
```

**Test 2 — FTP large-file threshold routes files to correct queues:**

Use `_NoStartThread` so queues are never drained — contents are directly inspectable afterward.
Assertions use `qsize()` + set membership to avoid order assumptions:

```python
_NoStartThread._instances.clear()
win = _make_ftp_window(tmp_path)
win.download_workers = 1
win.workers_var = _Var(1)
win.download_large_mb = 10
win.large_mb_var = _Var(10)
file_list = [("/large.bin", 15 * 1024 * 1024), ("/small.txt", 100)]
with patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
    win._download_thread_fn(file_list)

# Guard: worker_count=1 → exactly 2 threads (1 small + 1 large)
assert len(_NoStartThread._instances) == 2, (
    f"Expected 2 consumer threads, got {len(_NoStartThread._instances)}"
)
# First N-1 threads target q_small; last thread targets q_large (matches thread-append order)
q_small = _NoStartThread._instances[0]._args[0]
q_large = _NoStartThread._instances[-1]._args[0]
# Sanity: the two queues must be distinct objects
assert q_small is not q_large

# Drain each queue into a set of paths (order-independent)
def _drain_paths(q):
    paths = set()
    while True:
        try:
            paths.add(q.get_nowait()[0])
        except queue.Empty:
            break
    return paths

assert _drain_paths(q_large) == {"/large.bin"}
assert _drain_paths(q_small) == {"/small.txt"}
```

**Test 3 — HTTP worker count starts correct thread count:**

`download_workers=2` → 2 consumer threads.

```python
_NoStartThread._instances.clear()
win = _make_http_window(tmp_path)
win.download_workers = 2
win.workers_var = _Var(2)
with patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
    win._download_thread_fn([])
assert len(_NoStartThread._instances) == 2
```

**Test 4 — HTTP no large-file routing; all files in one queue:**

`_NoStartThread` keeps queue full. Assert single queue holds both files regardless of order:

```python
_NoStartThread._instances.clear()
win = _make_http_window(tmp_path)
win.download_workers = 1
win.workers_var = _Var(1)
win.download_large_mb = 10
win.large_mb_var = _Var(10)
file_list = [("/large.bin", 15 * 1024 * 1024), ("/small.txt", 100)]
with patch("gui.components.unified_browser_window.threading.Thread", _NoStartThread):
    win._download_thread_fn(file_list)

assert len(_NoStartThread._instances) == 1   # single queue, single worker
q = _NoStartThread._instances[0]._args[0]
all_paths = _drain_paths(q)                   # reuse helper from test 2
assert all_paths == {"/large.bin", "/small.txt"}
```

---

## Critical files

| File | Scope of change |
|------|-----------------|
| `gui/components/unified_browser_window.py` | Lines 622, 918–1031 (FTP), 1147, 1401–1511 (HTTP) |
| `gui/tests/test_browser_clamav.py` | `_make_ftp_window`, `_make_http_window`, 3 FTP tests updated, new class added |
| `gui/tests/test_ftp_browser_window.py` | `_make_ftp_with_settings`: add `load_probe_result_for_host` patch in constructor context |
| `gui/tests/test_http_browser_window.py` | `_make_http_with_settings`: same |

---

## Risks / assumptions

- **FTP thread count:** `worker_count=1` always produces **2 consumer threads** (1 × q_small + 1 × q_large). This is a deliberate behavioral change from the prior strictly-sequential single-thread flow — two FTP connections may be established. The large-file consumer exits immediately via first-item-claim if no file exceeds the threshold, so the common case is effectively single-connection after handshake. This is not regression-equivalent to C1; note in HI steps.
- **`workers_var` / `large_mb_var` UI access from background thread:** matches existing SMB pattern. Try/except falls back to stored values, which are set in all test fixtures.
- **HTTP navigator thread-safety:** `HttpNavigator.download_file` is stateless (per-request). Sharing across workers is safe. Cancel propagates via shared `self._cancel_event`.
- **`_on_cancel`** still calls `self._navigator.cancel()` (the listing navigator). Worker navs share `self._cancel_event` directly — cancel is fully effective without changes to `_on_cancel`.

## Validation

```bash
python3 -m py_compile gui/components/unified_browser_window.py shared/ftp_browser.py shared/http_browser.py
./venv/bin/python -m pytest gui/tests/test_browser_clamav.py gui/tests/test_ftp_browser_window.py gui/tests/test_http_browser_window.py -q
```

Expected: all pass, no new failures.

## HI test needed

Yes.
1. FTP: multi-file download with `Workers=2`; verify both files appear in quarantine. Note: two FTP connections will be opened.
2. FTP: set threshold below a test file's size; download that large file; confirm it completes without error.
3. HTTP: multi-file download with `Workers=2`; verify both files downloaded, no large-file queue behavior observed.
