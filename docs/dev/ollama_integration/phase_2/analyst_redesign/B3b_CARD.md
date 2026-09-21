# B3b — Interruptible inventory + progress

Status: HELD for DA (codex). One card. Commit per card, no push. Builds on B1/B2/B3a (committed).

## Problem / goal

Launching a directory run inventories the whole tree (hashing every file) on a worker thread while
the GUI shows a static "Inventorying and creating the durable run…" label with no count and no way
to stop it. On a large dir (DOE, >10k files) this looks hung. `create_and_launch` doesn't even
forward a `cancel_check`, so the uninterruptible hash cannot be aborted.

Give the operator (1) live progress ("Inventorying… N files") so a big dir doesn't look frozen, and
(2) a Cancel button that aborts the inventory phase. `inventory_tree` already accepts `cancel_check`
and raises `InventoryCancelled`; this card wires it through and adds a progress callback.

This does NOT make DOE-scale runs COMPLETE (the memory/throughput rework stays deferred) — it makes
the inventory phase visible and cancellable.

## Changes

### 1. Inventory progress — `experimental/analyst/inventory.py`

- Add `INVENTORY_PROGRESS_INTERVAL = 512` near the module limits.
- Add `progress_callback: ProgressCallback | None = None` to `_Walker.__init__` and store it
  (define `ProgressCallback = Callable[[int], None]` alongside the existing `CancelCheck` type).
- In `_Walker.walk`, right after `self.entries_seen += 1` (inventory.py:120), when
  `self.progress_callback is not None and self.entries_seen % INVENTORY_PROGRESS_INTERVAL == 0`,
  call `self.progress_callback(self.entries_seen)`. Keep `_check_cancel()` exactly as-is.
- Add `progress_callback: ProgressCallback | None = None` to `inventory_tree(...)` (inventory.py:220)
  and pass it into `_Walker(...)`. The callback must never raise into the walk — but the GUI callback
  is trivial (schedules a label update); do not add try/except around each call (keep it lean; the
  contract is that the callback is non-throwing).

### 2. Forward cancel + progress — `experimental/analyst/service.py`

- `create_directory_run` (service.py:282) already takes `cancel_check`; add
  `progress_callback: Callable[[int], None] | None = None` and pass it to `inventory_tree(...)`
  (service.py:308) alongside `cancel_check`.
- `create_and_launch` (service.py:431) currently calls `create_directory_run` with NEITHER. Add
  `cancel_check: Callable[[], bool] | None = None` and
  `progress_callback: Callable[[int], None] | None = None` params and forward both to
  `create_directory_run`. Manifest runs (`create_manifest_and_launch`) are unchanged (they do not
  inventory a tree).

### 3. GUI progress + Cancel — `gui/components/experimental_features/analyst_tab.py`

- In the launch controls (near the Analyze button, analyst_tab.py:214-218) add a Cancel button,
  hidden by default:
  ```
  self._cancel_launch_btn = tk.Button(
      controls, text="Cancel", state="normal", command=self._cancel_launch,
  )
  self._theme.apply_to_widget(self._cancel_launch_btn, "button_danger")
  # not packed here — shown only during a launch
  ```
  Add `self._launch_cancel_event = None` in `__init__`.
- `_start_analysis` (analyst_tab.py:1002): for the directory kind, create a fresh cancel event and
  show the button; pass `cancel_check` + `progress_callback` into `create_and_launch`:
  ```
  import threading
  self._launch_cancel_event = threading.Event()
  self._cancel_launch_btn.pack(side=tk.LEFT, padx=(0, 7))

  def _progress(seen: int) -> None:
      self._schedule(lambda: self._status_var.set(f"Inventorying… {seen} files"))
  ...
  launch = create_and_launch(
      request, model_tag=model_tag, model_digest=model_digest,
      cancel_check=self._launch_cancel_event.is_set,
      progress_callback=_progress,
  )
  ```
  (Manifest branch stays as-is.) In the work thread's `except` handler, show a friendly cancelled
  message when the operator cancelled:
  ```
  except Exception as exc:
      if self._launch_cancel_event is not None and self._launch_cancel_event.is_set():
          message = "Inventory cancelled."
      else:
          message = _creation_failure_message(exc)
      self._schedule(lambda: self._finish_action(False, message))
      return
  ```
- Add `_cancel_launch`:
  ```
  def _cancel_launch(self) -> None:
      event = self._launch_cancel_event
      if event is not None:
          event.set()
          self._status_var.set("Cancelling inventory…")
  ```
- Hide the Cancel button whenever a launch finishes: in `_finish_action` (analyst_tab.py:1356), call
  `self._cancel_launch_btn.pack_forget()` (safe even when it was never shown; harmless for the
  delete/abandon/resume actions that also route through `_finish_action`).

## Tests

- `shared/tests/test_analyst_b3b.py`: `inventory_tree` on a tree with > INVENTORY_PROGRESS_INTERVAL
  entries invokes `progress_callback` with monotonically increasing counts (use a small
  `INVENTORY_PROGRESS_INTERVAL` via a tree big enough, or assert the callback fired with the entry
  count); a `cancel_check` that returns True raises `InventoryCancelled`. Assert `create_and_launch`
  forwards `cancel_check`/`progress_callback` to `create_directory_run` (monkeypatch
  `create_directory_run` to capture kwargs; stub `launch_run`).
- `gui/tests/test_analyst_b3b.py` (xvfb, `gui_smoke`): `_start_analysis` (directory kind) shows the
  Cancel button and passes a callable `cancel_check` + `progress_callback` into a monkeypatched
  `create_and_launch`; invoking the progress callback updates the status label to "Inventorying… N
  files"; clicking Cancel sets the event; a raised inventory error while the event is set yields
  "Inventory cancelled."; `_finish_action` hides the Cancel button.
- Existing suites green: `./venv/bin/python -m pytest shared/tests -k analyst -q` and
  `xvfb-run -a ./venv/bin/python -m pytest gui/tests -k analyst -q` (run areas separately).

## Out of scope

The memory/throughput rework so DOE-scale dirs COMPLETE, and a distinct `failed` state / worker
failure-reason string (deferred). Do not change inventory limits or hashing.
