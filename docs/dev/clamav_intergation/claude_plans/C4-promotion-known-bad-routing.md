# C4: Promotion + Known-Bad Routing

## Context

C1–C3 are complete. C3 wires ClamAV scanning into the bulk extract path and sets `destination` intent ("extracted", "known_bad", "quarantine") in `PostProcessResult`, but `moved=False` for all verdicts — the actual file move was deferred. C4 performs those moves.

---

## Issue

Clean and infected files downloaded to quarantine are never relocated; the `destination` field in `PostProcessResult` is intent-only.

## Root cause

`build_clamav_post_processor` in [gui/utils/extract_runner.py](gui/utils/extract_runner.py) (line 64) scans each file and returns verdict + destination, but calls no move operation. `moved=False` for all results.

## Fix

Introduce `shared/quarantine_promotion.py` with two helpers (`resolve_promotion_dest`, `safe_move`) and a `PromotionConfig` dataclass. Extend `build_clamav_post_processor` to accept an optional `PromotionConfig`; when present, route the file after scanning. Build `PromotionConfig` in `run_extract` from `download_dir` + sanitized config. Update `_update_clamav_accum` to surface move errors. Update two C3 tests whose counter assertions have "C4 not yet" comments.

---

## Files changed

| File | Action |
|---|---|
| `shared/quarantine_promotion.py` | **new** — `PromotionConfig`, `resolve_promotion_dest`, `safe_move` |
| `shared/quarantine_postprocess.py` | **modify** — update `PostProcessResult.error` field comment (see below) |
| `gui/utils/extract_runner.py` | **modify** — sanitizer, builder, run_extract setup, accumulator |
| `shared/tests/test_quarantine_promotion.py` | **new** — unit tests for helpers |
| `gui/tests/test_extract_runner_clamav.py` | **modify** — update 2 tests with "C4 not yet" comments; add routing seam tests |

---

## Proposed helper API (`shared/quarantine_promotion.py`)

```python
@dataclass
class PromotionConfig:
    ip_address: str
    date_str: str          # "YYYYMMDD", validated from download_dir.name or fallback
    quarantine_root: Path  # validated from download_dir.parent.parent or fallback
    extracted_root: Path   # ~/.dirracuda/extracted (or override)
    known_bad_subdir: str  # sanitized single-segment label, default "known_bad"
    download_dir: Path     # the actual quarantine dir files landed in (for rel_path derivation)


def resolve_promotion_dest(
    verdict: str,          # "clean" | "infected" | "error"
    file_path: Path,       # actual on-disk path of the downloaded file
    share: str,
    cfg: PromotionConfig,
) -> Optional[Path]:
    """Returns target Path or None (no move; leave in quarantine).

    local_rel_path is derived as file_path.relative_to(cfg.download_dir / share),
    never from display metadata.

    # "clean"    → cfg.extracted_root / safe_host / date / safe_share / rel
    # "infected" → cfg.quarantine_root / safe_subdir / safe_host / date / safe_share / rel
    # "error"    → None
    """


def safe_move(src: Path, dest: Path) -> Path:
    """
    Move src → dest. Creates dest.parent dirs.
    Collision: appends _1, _2 … _99 to stem until unique.
    Returns actual dest path used.
    Raises OSError on failure (caller wraps in fail-open try/except).
    """
```

**Sanitization**: `quarantine_promotion.py` defines a module-private `_sanitize_segment(value: str) -> str` with the same character-filter logic as `quarantine.py:_sanitize_label` (alphanum + `-_.`, strip leading/trailing, fallback label). It does **not** import the private `_sanitize_label` symbol. Slight duplication is acceptable for one standalone function; avoids brittle private-symbol coupling.

---

## Path derivation

All inputs are available at `run_extract` + `PostProcessInput` time:

| Piece | Source |
|---|---|
| `ip_address` | `run_extract` parameter; also in `PostProcessInput.ip_address` |
| `date_str` | `download_dir.name`, validated below |
| `quarantine_root` | `download_dir.parent.parent`, validated below |
| `extracted_root` | `clamav_config["extracted_root"]` (default `"~/.dirracuda/extracted"`) |
| `known_bad_subdir` | `clamav_config["known_bad_subdir"]` sanitized through `_sanitize_segment` |
| `download_dir` | passed directly into `PromotionConfig` for rel_path derivation |
| `local_rel_path` | `inp.file_path.relative_to(cfg.download_dir / inp.share)` — derived from actual on-disk path, not display metadata |

`PromotionConfig` is built once per `run_extract` call and captured in the processor closure.

### Validation/fallbacks when building `PromotionConfig`

**`date_str`**: Check `download_dir.name` matches `r"^\d{8}$"`. If not, fall back to `datetime.utcnow().strftime("%Y%m%d")`.

**`quarantine_root`**: Compute `candidate = download_dir.parent.parent`. Check that `candidate != candidate.parent` (i.e. not filesystem root). If it is root (e.g. `Path("/a/b")` → `parent.parent` = `/`), fall back to `Path.home() / ".dirracuda" / "quarantine"`. The `len(parts) >= 3` check alone is insufficient because a 3-part absolute path like `/a/b` still yields `/` as its grandparent.

**`known_bad_subdir`**: Pass through `_sanitize_segment`. Since `_sanitize_segment` also rejects `/` and `\`, a value like `"../../outside"` becomes a safe single-label string. An empty result falls back to `"known_bad"`.

---

## Changes to `shared/quarantine_postprocess.py`

Update the `error` field comment on `PostProcessResult` (line 24). Current: `"only when verdict == 'error'"`. New: `"set when verdict == 'error' OR when a move failed for 'clean'/'infected' verdicts"`. No runtime logic changes; docstring/comment only. Any test assertions that check `result.error is None` for clean/infected results must be relaxed if they now exercise C4 paths.

---

## Changes to `_sanitize_clamav_config`

Add two new keys:
```python
"extracted_root": str(raw.get("extracted_root", "~/.dirracuda/extracted")),
"known_bad_subdir": str(raw.get("known_bad_subdir", "known_bad")),
```

---

## Changes to `build_clamav_post_processor`

Update docstring: remove C3 comment "File routing to extracted/ or known_bad/ is deferred to C4. C3 sets verdict and destination intent only; moved=False for all results." Replace with C4 behavior: "When promotion_cfg is provided, clean files are moved to extracted root and infected files to known_bad subtree. moved=True on successful move. move failures return moved=False with error set."

New signature:
```python
def build_clamav_post_processor(
    clamav_cfg: Dict[str, Any],
    promotion_cfg: Optional[PromotionConfig] = None,
) -> PostProcessorFn:
```

Inside `_scan` closure, after scan result is obtained:
- `"error"` verdict → return immediately (no move), file stays in quarantine, `moved=False`
- `"infected"` / `"clean"` → if `promotion_cfg` is set, wrap **both** `resolve_promotion_dest(...)` **and** `safe_move(...)` in a single inner `try/except`:
  - This prevents unexpected path-shape errors (e.g. `relative_to()` mismatch) from bubbling to the outer seam and skipping `_update_clamav_accum`, which would cause scan counter drift.
  - Success: return `PostProcessResult(final_path=actual, moved=True, ...)`
  - Any exception: return `PostProcessResult(final_path=inp.file_path, moved=False, error=f"move failed: {exc}", ...)`
- If `promotion_cfg` is `None`: return current C3 behavior unchanged (`moved=False`)

---

## New helper: `build_promotion_config` (in `extract_runner.py`)

All validation and fallback logic is centralized in a small private helper so `run_extract` never constructs `PromotionConfig` from raw values, and the validation is independently testable:

```python
def _build_promotion_config(
    ip_address: str,
    download_dir: Path,
    sanitized_cfg: Dict[str, Any],
) -> PromotionConfig:
    # date_str: validate YYYYMMDD; fallback to utcnow
    date_str = download_dir.name
    if not re.match(r"^\d{8}$", date_str):
        date_str = datetime.utcnow().strftime("%Y%m%d")

    # quarantine_root: guard against filesystem root as grandparent
    candidate = download_dir.parent.parent
    if candidate == candidate.parent:  # reached filesystem root
        candidate = Path.home() / ".dirracuda" / "quarantine"

    return PromotionConfig(
        ip_address=ip_address,
        date_str=date_str,
        quarantine_root=candidate,
        extracted_root=Path(sanitized_cfg["extracted_root"]).expanduser(),
        known_bad_subdir=sanitized_cfg["known_bad_subdir"],  # already sanitized by _sanitize_clamav_config
        download_dir=download_dir,
    )
```

## Changes to `run_extract`

After `_safe_cfg = _sanitize_clamav_config(clamav_config)`:
```python
_prom_cfg = _build_promotion_config(ip_address, download_dir, _safe_cfg)
_active_pp = build_clamav_post_processor(_safe_cfg, promotion_cfg=_prom_cfg)
```

No changes to the fail-open `try/except` seam — move failures inside `_scan` return a `PostProcessResult` with `error` set and `moved=False` (not an exception), so they flow through the accumulator path, not the exception path.

---

## Changes to `_update_clamav_accum`

Surface move failures (non-None `result.error` when verdict is "clean" or "infected"):
```python
if result.verdict == "clean":
    accum["clean"] += 1
    if result.moved:
        accum["promoted"] += 1
    elif result.error is not None:
        accum["errors"] += 1
        accum["error_items"].append({"path": rel_display, "error": result.error})

elif result.verdict == "infected":
    accum["infected"] += 1
    if result.moved:
        accum["known_bad_moved"] += 1
    elif result.error is not None:
        accum["errors"] += 1
        accum["error_items"].append({"path": rel_display, "error": result.error})
    ...
```

---

## Collision policy

`safe_move` tries `dest` first. If it exists, tries `dest.stem + "_1" + dest.suffix`, `"_2"` … up to `"_99"`. Raises `FileExistsError("collision limit reached")` after 99 attempts. Caller wraps in fail-open try/except → move failure → file stays in quarantine.

---

## How move failures appear in summary

Move failures surface as entries in `clamav["error_items"]` (via updated `_update_clamav_accum`). The verdict counter (`clean` / `infected`) still increments so the scan outcome is preserved. `moved` is `False`, so `promoted` / `known_bad_moved` do not increment. The `error_items` entry reads: `{"path": rel_display, "error": "move failed: <reason>"}`.

---

## C3 test updates (`gui/tests/test_extract_runner_clamav.py`)

Two tests have "C4 not yet" comments and will fail after C4 unless updated:

### `test_enabled_clean_verdict_updates_summary` (line ~190)
- Add `"extracted_root": str(tmp_path / "extracted")` to `clamav_config`
- Change `assert cv["promoted"] == 0` → `assert cv["promoted"] == 1`
- Add: `assert (tmp_path / "extracted" / "1.2.3.4" / "20260328" / "pub" / "a.txt").exists()`
- Add: `assert not (download_dir / "pub" / "a.txt").exists()` (file was moved)

### `test_enabled_infected_verdict_updates_summary` (line ~225)
- Add `"extracted_root": str(tmp_path / "extracted")` to `clamav_config` (for consistency)
- Change `assert cv["known_bad_moved"] == 0` → `assert cv["known_bad_moved"] == 1`
- Add: known_bad path assertion: `(tmp_path / "q" / "known_bad" / "1.2.3.4" / "20260328" / "pub" / "a.txt").exists()`

All other C3 tests unaffected (error/exception paths have `moved=False` regardless).

---

## New test cases (`shared/tests/test_quarantine_promotion.py`)

Unit tests for helpers (pure Python, no network, no impacket):

1. **`test_resolve_clean_returns_extracted_path`** — clean verdict → path under `extracted_root`
2. **`test_resolve_infected_returns_known_bad_path`** — infected verdict → path under `quarantine_root/known_bad`
3. **`test_resolve_error_returns_none`** — error verdict → `None`
4. **`test_resolve_nested_rel_path`** — file nested in share subdir; derived via `file_path.relative_to(download_dir/share)`, not rel_display; subdirs preserved in target
5. **`test_resolve_ip_sanitized`** — colons/dots in IP sanitized to safe label
6. **`test_resolve_uses_file_path_not_rel_display`** — file_path and rel_display disagree; assert destination uses file_path derivation
7. **`test_build_promotion_config_date_fallback_on_invalid_name`** — `download_dir.name` = `"notadate"` → date_str falls back to today's `YYYYMMDD`
8. **`test_build_promotion_config_quarantine_root_fallback_on_filesystem_root`** — `download_dir = Path("/a/b")` → `parent.parent` = `/` which equals its own parent → quarantine_root falls back to `~/.dirracuda/quarantine`
9. **`test_build_promotion_config_known_bad_subdir_sanitized`** — `"../../outside"` → sanitized to safe label; `""` or all-invalid → `"known_bad"` fallback
10. **`test_safe_move_creates_parent_dirs`** — dest parent does not exist → created and move succeeds
11. **`test_safe_move_no_collision`** — dest does not exist → returns dest unchanged
12. **`test_safe_move_collision_appends_suffix`** — dest exists → returns `stem_1.ext`
13. **`test_safe_move_collision_chain`** — `stem_1` also exists → returns `stem_2.ext`
14. **`test_safe_move_raises_at_limit`** — all 99 suffix slots occupied → raises `FileExistsError`
15. **`test_safe_move_raises_on_os_error`** — `shutil.move` raises `OSError` → propagates

New seam tests in `gui/tests/test_extract_runner_clamav.py`:

17. **`test_c4_clean_file_promoted_to_extracted`** — full `run_extract` with clean scan + routing (extracted_root in tmp_path); asserts `saved_to` matches extracted path, `promoted==1`, file exists at target, original quarantine path gone
18. **`test_c4_infected_file_moved_to_known_bad`** — full `run_extract` with infected scan + routing; asserts `saved_to` matches known_bad path, `known_bad_moved==1`
19. **`test_c4_error_verdict_stays_in_quarantine`** — error scan; asserts file unchanged at original quarantine path, `moved=False`
20. **`test_c4_move_failure_recorded_in_error_items`** — monkeypatch `safe_move` to raise; asserts `error_items` has entry, `promoted==0`, file at original quarantine path
21a. **`test_c4_resolve_raises_caught_in_accumulator`** — monkeypatch `resolve_promotion_dest` to raise `ValueError`; asserts scan counter (`clean`) still increments, `promoted==0`, `error_items` has entry (verifies inner try/except prevents outer-seam bypass and counter drift)
21. **`test_c4_collision_resolved`** — pre-create destination file; asserts downloaded file gets `_1` suffix at target

---

## ClamAV disabled / precedence

No behavior change:
- `clamav_config=None` → `_active_pp=None`, no processor, no routing (unchanged)
- `clamav_config={"enabled": False}` → same (unchanged)
- `post_processor` set explicitly → `_active_pp = post_processor`, `_prom_cfg` never built (unchanged — C3 precedence preserved)

---

## Validation run

```bash
python3 -m py_compile shared/quarantine_promotion.py gui/utils/extract_runner.py
./venv/bin/python -m pytest shared/tests/test_quarantine_promotion.py gui/tests/test_extract_runner_clamav.py -q
```

## HI test needed: Yes

Steps:
1. Enable ClamAV in `conf/config.json`
2. Place EICAR test file on an accessible share
3. Run bulk extract targeting that host
4. Verify clean files appear under `~/.dirracuda/extracted/<host>/<date>/<share>/`
5. Verify EICAR appears under `~/.dirracuda/quarantine/known_bad/<host>/<date>/<share>/`
6. Confirm scanner-error files remain in `~/.dirracuda/quarantine/<host>/<date>/`
7. Disable ClamAV; re-run; confirm no files moved and no errors
