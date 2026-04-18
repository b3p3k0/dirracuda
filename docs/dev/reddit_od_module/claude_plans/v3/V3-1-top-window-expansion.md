# Plan: Card V3-1 — Top Window Expansion

## Context

`feed top` requests currently hardcode `t=week` in `client.py:68`. V3-1 makes the top window
configurable (hour/day/week/month/year/all) across the client, service options, and dialog UI.
The ingest-state key moves from `"top"` to `"top:<window>"` with a read-fallback for the legacy
`"top"` key when window is `week`, preserving historical state continuity.

---

## Card Summary

Add a `top_window` parameter that flows from the dialog → `IngestOptions` → `service._run_top` →
`client.fetch_posts/fetch_page`. Ingest state is saved under `"top:<window>"`. Legacy `"top"` key
is readable as a fallback only for `week`. No schema change needed.

---

## File Touch List

| File | Change |
|------|--------|
| `experimental/redseek/client.py` (149 ln) | Add `top_window="week"` param to `fetch_page` and `fetch_posts`; pass to `params["t"]` |
| `experimental/redseek/service.py` (460 ln) | Add `top_window: str = "week"` to `IngestOptions`; add validation; build `"top:<window>"` key in `_run_top` (new save + new read path for legacy fallback); pass to `fetch_posts` |
| `gui/components/reddit_grab_dialog.py` (204 ln) | Add `top_window_var`; add top-window combobox (enabled only when sort=top); pass to `IngestOptions` in `_validate` |
| `shared/tests/test_redseek_client.py` (338 ln) | Update `test_fetch_page_top_sort_includes_t_week`; add parametrized window tests; add `new` sort regression test |
| `shared/tests/test_redseek_service.py` (604 ln) | Update `test_top_updates_scrape_time_only` (state key changes); add three new top-window tests with mock-asserted call order |
| `gui/tests/test_reddit_grab_dialog.py` (**new file**) | Dialog behavior tests: top-window enable/disable on sort change; `IngestOptions.top_window` propagation |

`models.py` and `store.py` — **no changes**. `RedditIngestState.sort_mode` is an untyped `str`;
`get_ingest_state`/`save_ingest_state` pass the key string verbatim. The new `"top:week"` value
requires no schema or store changes.

---

## Implementation Steps

### Step 1 — `client.py`: parameterise `top_window`

**`fetch_page` (line 57 signature, line 68 hardcode):**
```python
# before
def fetch_page(sort: str, after: Optional[str] = None, timeout: int = 20) -> PageResult:
    ...
    if sort == "top":
        params["t"] = "week"

# after
def fetch_page(
    sort: str,
    after: Optional[str] = None,
    timeout: int = 20,
    top_window: str = "week",
) -> PageResult:
    ...
    if sort == "top":
        params["t"] = top_window
```

**`fetch_posts` (line 113 signature, calls fetch_page):**
```python
# before
def fetch_posts(sort: str, max_pages: int = 3, timeout: int = 20) -> FetchResult:
    ...
    result = fetch_page(sort, after=after, timeout=timeout)

# after
def fetch_posts(
    sort: str,
    max_pages: int = 3,
    timeout: int = 20,
    top_window: str = "week",
) -> FetchResult:
    ...
    # All kwargs — enforces a stable call shape that the passthrough test can assert exactly
    result = fetch_page(sort=sort, after=after, timeout=timeout, top_window=top_window)
```

---

### Step 2 — `service.py`: extend `IngestOptions` + build `"top:<window>"` key

**`IngestOptions` dataclass — add field (line ~54):**
```python
top_window: str = "week"   # only used when sort="top"
```

**Validation block in `run_ingest` (line ~418-431) — add after existing sort check:**
```python
VALID_TOP_WINDOWS = {"hour", "day", "week", "month", "year", "all"}
if options.sort == "top" and options.top_window not in VALID_TOP_WINDOWS:
    return _error_result(options, False, error=f"invalid top_window: {options.top_window!r}")
```

**`run_ingest` call to `fetch_posts` (line ~451) — pass top_window:**
```python
fetch_result = fetch_posts(
    options.sort,
    max_pages=options.max_pages,
    top_window=options.top_window,
)
```

**`_run_top`: add new read path + update save key.**

`_run_top` currently has **no** `get_ingest_state` read — it only saves at line 367. Two changes:

**Add new migration block** at the top of the `try:` block (before the `for raw in
fetch_result.posts` loop, after the counters are zeroed). For `week`, explicitly copy the legacy
`"top"` row into the new key so the historical state is preserved and the fallback read is not a
dead no-op:
```python
window_key = f"top:{options.top_window}"
if options.top_window == "week":
    _existing = get_ingest_state(conn, options.subreddit, window_key)
    if _existing is None:
        _legacy = get_ingest_state(conn, options.subreddit, "top")
        if _legacy is not None:
            # One-time migration: copy legacy 'top' row to 'top:week'
            # so the historical last_scrape_time survives the key rename.
            save_ingest_state(
                conn,
                RedditIngestState(
                    subreddit=_legacy.subreddit,
                    sort_mode=window_key,
                    last_post_created_utc=_legacy.last_post_created_utc,
                    last_post_id=_legacy.last_post_id,
                    last_scrape_time=_legacy.last_scrape_time,
                ),
            )
```

This is a real write, not a dead read. The main `save_ingest_state` at the end of the function
then overwrites `last_scrape_time` with the current run's time — which is correct. The legacy
`"top"` row is left in place (no delete) as a tombstone.

**Update the existing `save_ingest_state` call** (line 367-376): change `sort_mode="top"` to
`sort_mode=window_key`:
```python
save_ingest_state(
    conn,
    RedditIngestState(
        subreddit=options.subreddit,
        sort_mode=window_key,          # was: "top"
        last_post_created_utc=None,
        last_post_id=None,
        last_scrape_time=now_str,
    ),
)
```

`IngestResult.sort` at line 379 stays `"top"` — it is the human-facing sort label, not the
state key.

---

### Step 3 — `reddit_grab_dialog.py`: add top-window combobox

**Add var (alongside existing vars ~line 43-47):**
```python
self.top_window_var = tk.StringVar(value="week")
```

**Add combobox widget (after sort row ~line 56-65):**
```python
ttk.Label(frm, text="Top Window:").grid(row=0, column=2, sticky="e", padx=(12, 4))
self._top_window_cb = ttk.Combobox(
    frm,
    textvariable=self.top_window_var,
    values=["hour", "day", "week", "month", "year", "all"],
    state="disabled",
    width=8,
)
self._top_window_cb.grid(row=0, column=3, sticky="w")
```

**Bind sort combobox to toggle top-window state:**
```python
self.sort_var.trace_add("write", self._on_sort_changed)

def _on_sort_changed(self, *_):
    if self.sort_var.get() == "top":
        self._top_window_cb.configure(state="readonly")
    else:
        self._top_window_cb.configure(state="disabled")
        self.top_window_var.set("week")   # reset to default on switch away
```

Also call `self._on_sort_changed()` at end of `__init__` to set initial state.

**`_validate` — pass `top_window` into `IngestOptions`:**
```python
return IngestOptions(
    sort=sort,
    max_posts=max_posts,
    parse_body=self.parse_body_var.get(),
    include_nsfw=self.include_nsfw_var.get(),
    replace_cache=self.replace_cache_var.get(),
    top_window=self.top_window_var.get(),
)
```

---

### Step 4 — `test_redseek_client.py`: update + extend top-window tests

Ensure `from unittest.mock import ANY, patch` is present at the top of the file (add `ANY` if not
already imported).

**Update existing test (line 179):**
```python
# rename to:
def test_fetch_page_top_sort_default_window_is_week():
    # same body, assertion stays: assert "t=week" in req.full_url
```

**Add parametrized test:**
```python
@pytest.mark.parametrize("window", ["hour", "day", "week", "month", "year", "all"])
def test_fetch_page_top_sort_includes_correct_t_param(window):
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("top", top_window=window)
    req = mock_open.call_args[0][0]
    assert f"t={window}" in req.full_url
```

**Add `fetch_posts` passthrough test:**
```python
def test_fetch_posts_passes_top_window_to_fetch_page():
    with patch("experimental.redseek.client.fetch_page") as mock_fp:
        mock_fp.return_value = PageResult(posts=[], next_after=None)
        fetch_posts("top", max_pages=1, top_window="month")
    # All-kwarg assertion is stable because fetch_posts calls fetch_page with
    # all-kwargs (sort=sort, after=after, timeout=timeout, top_window=top_window)
    mock_fp.assert_called_once_with(sort="top", after=None, timeout=ANY, top_window="month")
```

**Add `new` sort regression test** (guard that the refactor does not introduce `t=` for new):
```python
def test_fetch_page_new_sort_does_not_emit_t_param():
    resp = _mock_resp(_make_payload([]))
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        fetch_page("new")
    req = mock_open.call_args[0][0]
    assert "t=" not in req.full_url
```

Note: the existing `test_fetch_page_new_sort_does_not_include_t_week` (line 187) checks only for
`t=week`; this new test is stricter — `t=` must not appear at all. Keep both.

---

### Step 5 — `test_redseek_service.py`: update broken test + add three new

**Update `test_top_updates_scrape_time_only` (line 344):**
- Options now default `top_window="week"`, so state key becomes `"top:week"`.
- Update the `get_ingest_state` read assertion:
  ```python
  # before
  state = get_ingest_state(conn, "opendirectories", "top")
  # after
  state = get_ingest_state(conn, "opendirectories", "top:week")
  ```

**Add three new tests with explicit call-order assertions via monkeypatch:**

```python
def test_top_state_key_uses_window_prefix(tmp_path):
    """State is saved under 'top:<window>', not the legacy 'top' key."""
    # Run ingest with top_window="month" (any non-week window)
    options = IngestOptions(sort="top", max_posts=5, parse_body=False,
                            include_nsfw=False, replace_cache=False, top_window="month")
    run_ingest(options, db_path=tmp_path / "test.db")
    with open_connection(tmp_path / "test.db") as conn:
        assert get_ingest_state(conn, "opendirectories", "top:month") is not None
        assert get_ingest_state(conn, "opendirectories", "top") is None


def test_top_week_fallback_copies_legacy_to_new_key(tmp_path):
    """When top:week absent and legacy 'top' exists, migration copies row to 'top:week'."""
    db = tmp_path / "test.db"
    init_db(db)
    # Pre-seed legacy state
    with open_connection(db) as conn:
        save_ingest_state(conn, RedditIngestState(
            subreddit="opendirectories", sort_mode="top",
            last_post_created_utc=None, last_post_id=None,
            last_scrape_time="2025-01-01 00:00:00",
        ))
        conn.commit()

    options = IngestOptions(sort="top", max_posts=5, parse_body=False,
                            include_nsfw=False, replace_cache=False, top_window="week")
    run_ingest(options, db_path=db)

    with open_connection(db) as conn:
        new_state = get_ingest_state(conn, "opendirectories", "top:week")
        legacy_state = get_ingest_state(conn, "opendirectories", "top")
    # Migration wrote top:week; legacy row still present as tombstone
    assert new_state is not None
    assert legacy_state is not None  # tombstone, not deleted


def test_top_week_migration_skipped_when_new_key_already_exists(tmp_path, monkeypatch):
    """Migration block is a no-op when top:week already present — legacy is not recopied."""
    import experimental.redseek.service as svc
    save_calls = []
    real_save = svc.save_ingest_state

    def tracking_save(conn, state):
        save_calls.append(state.sort_mode)
        return real_save(conn, state)

    monkeypatch.setattr(svc, "save_ingest_state", tracking_save)

    db = tmp_path / "test.db"
    init_db(db)
    # Pre-seed both keys so top:week already exists
    with open_connection(db) as conn:
        save_ingest_state(conn, RedditIngestState(
            subreddit="opendirectories", sort_mode="top",
            last_post_created_utc=None, last_post_id=None,
            last_scrape_time="2025-01-01 00:00:00",
        ))
        save_ingest_state(conn, RedditIngestState(
            subreddit="opendirectories", sort_mode="top:week",
            last_post_created_utc=None, last_post_id=None,
            last_scrape_time="2025-06-01 00:00:00",
        ))
        conn.commit()

    options = IngestOptions(sort="top", max_posts=5, parse_body=False,
                            include_nsfw=False, replace_cache=False, top_window="week")
    run_ingest(options, db_path=db)

    # Only the main save should have written top:week — migration write must not appear
    assert save_calls.count("top:week") == 1  # exactly one write: the main save


def test_top_non_week_no_legacy_fallback(tmp_path, monkeypatch):
    """For non-week windows, the legacy 'top' key is never queried."""
    import experimental.redseek.service as svc
    calls = []
    real_get = svc.get_ingest_state

    def tracking_get(conn, subreddit, sort_mode):
        calls.append(sort_mode)
        return real_get(conn, subreddit, sort_mode)

    monkeypatch.setattr(svc, "get_ingest_state", tracking_get)

    options = IngestOptions(sort="top", max_posts=5, parse_body=False,
                            include_nsfw=False, replace_cache=False, top_window="month")
    run_ingest(options, db_path=tmp_path / "test.db")
    assert "top" not in calls      # legacy key never queried
    assert "top:month" in calls    # only the scoped key
```

---

### Step 6 — `gui/tests/test_reddit_grab_dialog.py`: new file, headless dialog behavior tests

Use `RedditGrabDialog.__new__(RedditGrabDialog)` to skip `__init__` entirely — no real
`tk.Tk()`, no display needed. Match the `__new__` + `MagicMock` pattern established in
`test_experimental_features_dialog.py`.

```python
from unittest.mock import MagicMock
import sys, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# stub impacket if not present (same boilerplate as other GUI tests)
if "impacket" not in sys.modules:
    ...  # same stub block as test_experimental_features_dialog.py

from gui.components.reddit_grab_dialog import RedditGrabDialog


def _make_dialog():
    """Minimal RedditGrabDialog with all tk vars/widgets replaced by MagicMocks."""
    d = RedditGrabDialog.__new__(RedditGrabDialog)
    d.sort_var = MagicMock()
    d.sort_var.get.return_value = "new"
    d.top_window_var = MagicMock()
    d.top_window_var.get.return_value = "week"
    d._top_window_cb = MagicMock()
    d.max_posts_var = MagicMock()
    d.max_posts_var.get.return_value = "50"
    d.parse_body_var = MagicMock()
    d.parse_body_var.get.return_value = True
    d.include_nsfw_var = MagicMock()
    d.include_nsfw_var.get.return_value = False
    d.replace_cache_var = MagicMock()
    d.replace_cache_var.get.return_value = False
    d.dialog = MagicMock()   # guard _validate messagebox if called
    return d


def test_top_window_combobox_disabled_when_sort_new():
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d._on_sort_changed()
    d._top_window_cb.configure.assert_called_with(state="disabled")


def test_top_window_combobox_enabled_when_sort_top():
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d._on_sort_changed()
    d._top_window_cb.configure.assert_called_with(state="readonly")


def test_top_window_resets_to_week_when_sort_switched_back_to_new():
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d._on_sort_changed()
    d.top_window_var.set.assert_called_with("week")
    d._top_window_cb.configure.assert_called_with(state="disabled")


def test_validate_passes_top_window_in_ingest_options():
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d.top_window_var.get.return_value = "month"
    result = d._validate()
    assert result is not None
    assert result.top_window == "month"


def test_validate_top_window_defaults_to_week_for_top_sort():
    d = _make_dialog()
    d.sort_var.get.return_value = "top"
    d.top_window_var.get.return_value = "week"
    result = d._validate()
    assert result.top_window == "week"


def test_validate_new_sort_carries_top_window_field():
    """sort=new passes top_window through (service ignores it for new mode)."""
    d = _make_dialog()
    d.sort_var.get.return_value = "new"
    d.top_window_var.get.return_value = "week"
    result = d._validate()
    assert result.sort == "new"
    assert result.top_window == "week"
```

No display, no network, no real tk objects anywhere. `_make_dialog()` is the only shared fixture.

---

## Risks and Assumptions

| Risk | Mitigation |
|------|------------|
| `test_top_updates_scrape_time_only` reads `"top"` key — will fail after step 2 | Update assertion to `"top:week"` in step 5 |
| `_make_options()` in wiring test uses no `top_window` kwarg — IngestOptions default covers it | Default `top_window="week"` — no wiring test change needed |
| `source_sort` in `RedditPost` stores `"top"` — `_run_top` passes `options.sort` (`"top"`), not window key | Correct; `source_sort` is the human-facing label; leave unchanged |
| `replace_cache` calls `fetch_posts` before `_run_top` — `top_window` must be on `IngestOptions` | Covered: `top_window` on `IngestOptions`, passed in step 2's `fetch_posts` call |
| Legacy `"top"` row not deleted after migration — left as a tombstone in the DB | Acceptable; it is never written again. Forward writes are all under `"top:week"` |
| Migration write + main save both write `top:week` in the same transaction — `last_scrape_time` from legacy is immediately overwritten | Correct by design. Both writes share `conn`'s transaction; a rollback undoes both atomically. The migration write is not durable independently — it exists only to capture legacy state in the same run that first uses `top:week` |
| `ANY` not imported in `test_redseek_client.py` — `assert_called_once_with` would raise `NameError` | Add `from unittest.mock import ANY, patch` (or `ANY` to existing import) in step 4 |
| Dialog tests and CI display | Step 6 uses `__new__` + `MagicMock` only — no `tk.Tk()`, no display, no `Xvfb` needed. Consistent with headless pattern in `test_experimental_features_dialog.py` |

**Assumption:** `RedditPost.source_sort` continues to store `"top"` — confirmed: `_run_top` (line 349) hardcodes `source_sort="top"`. No change needed.

---

## Validation Commands

```bash
# 1. Syntax check all touched source files
./venv/bin/python -m py_compile \
  experimental/redseek/client.py \
  experimental/redseek/service.py \
  gui/components/reddit_grab_dialog.py

# 2. Run targeted test suites
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_reddit_grab_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q

# 3. Regression suite (no store/model changes, but verify no side-effects)
./venv/bin/python -m pytest \
  shared/tests/test_redseek_store.py \
  gui/tests/test_experimental_features_dialog.py -q
```

---

## PASS/FAIL Gates

| Gate | Command / Check | PASS Condition |
|------|-----------------|----------------|
| Syntax clean | `py_compile` on 3 source files | Zero exit, no output |
| Client: parametrized windows | `pytest test_redseek_client.py` | 6 parametrized `test_fetch_page_top_sort_includes_correct_t_param[*]` pass |
| Client: fetch_posts passthrough | `pytest test_redseek_client.py` | `test_fetch_posts_passes_top_window_to_fetch_page` passes |
| Client: new sort regression | `pytest test_redseek_client.py` | `test_fetch_page_new_sort_does_not_emit_t_param` passes (`t=` absent in URL) |
| Service: existing top test updated | `pytest test_redseek_service.py` | `test_top_updates_scrape_time_only` passes (reads `"top:week"` key) |
| Service: state key prefix | `pytest test_redseek_service.py` | `test_top_state_key_uses_window_prefix` passes |
| Service: week fallback migration | `pytest test_redseek_service.py` | `test_top_week_fallback_copies_legacy_to_new_key` passes; `test_top_week_migration_skipped_when_new_key_already_exists` passes (exactly 1 save call for `top:week`) |
| Service: non-week no legacy query | `pytest test_redseek_service.py` | `test_top_non_week_no_legacy_fallback` passes; `"top" not in calls` |
| Dialog: enable/disable lifecycle | `pytest test_reddit_grab_dialog.py` | All 6 dialog tests pass |
| Dialog: IngestOptions propagation | `pytest test_reddit_grab_dialog.py` | `test_validate_passes_top_window_in_ingest_options` passes |
| Wiring regression | `pytest test_dashboard_reddit_wiring.py` | All pass unchanged |
| Store regression | `pytest test_redseek_store.py` | All pass (no store changes) |
| File size | `wc -l` on touched source files | client.py ≲165, service.py ≲500, dialog ≲240 — all under 1200 |
