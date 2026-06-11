# C4 — SMB Extract Path Containment (Implementation Plan, Rev 3)

## Context

C4 of the 10 June 2026 security review. SMB bulk extract
(`gui/utils/extract_runner.py::run_extract`) uses the **server-supplied share
string directly as a local filesystem path component** when building each
download destination. A hostile SMB server can return a share name like
`../../etc` (or one containing slashes/backslashes/absolute prefixes); because
the share is concatenated into the local path with no sanitization, the resolved
destination can escape the download root before any `mkdir`/`open`. This is a
CWE-22 path-traversal write primitive against the operator's machine.

The fix introduces a clean **dual-identity contract**: an exact `remote_share`
used for every SMB call and all reporting, and a deterministic, order-
independent, single-segment `local_label` used for on-disk paths and promotion.
Containment and empty-path rejection happen immediately before any filesystem
mutation.

Revision history:
- Rev 1 → Rev 2 (RA): exact `remote_share` end-to-end + dedicated `local_share`
  field; order-independent allocation; reject empty sanitized relative paths.
- Rev 2 → Rev 3 (RA):
  1. **Medium** — collision allocation must prioritize the *exact safe owner*
     (`remote_share == sanitized base`) so a hostile alias like `../pub` cannot
     steal the base label of a normal `pub`.
  2. **Medium** — empty / separator-only shares are handled explicitly (rejected
     with reporting, no SMB call), not silently dropped; dot-only shares are
     retained and map to the fallback label.
  3. **Low** — add a test capturing a custom post-processor that asserts both
     `PostProcessInput` identities (exact `share`, safe `local_share`).

This plan is for C4 only. No code is edited in this session.

## Status and baseline

- Branch: `development`; HEAD: `8a3debca15b9f1bcac1988417c8e9ec4b76634e8`.
- Worktree: clean (untracked planning workspace only).
- Tracker: C0–C3 ACCEPTED; **C4 NOT STARTED** (unblocked; depends only on C3).
- Line counts now:
  - `gui/utils/extract_runner.py`: **825**
  - `shared/quarantine_postprocess.py`: 49
  - `shared/quarantine_promotion.py`: 122 (not modified)
  - `gui/tests/test_extract_runner_clamav.py`: **817**
  - `shared/tests/test_quarantine_postprocess.py`: 186
- Focused baseline (stated): 41 passed for `test_extract_runner_clamav.py` +
  `test_quarantine_postprocess.py`.
- Python: 3.13.7 here; runtime still supports 3.8 (R-12 deferred) → no 3.9+-only
  APIs. `Path.is_relative_to` is banned; use `relative_to` + `except`.

## Objective

Every file written by `run_extract` lands strictly under the resolved download
root, regardless of hostile share or file names; the exact remote share is
preserved for SMB calls and reporting; label allocation is deterministic,
order-independent, and never lets an alias steal a normal share's name; and
empty/degenerate inputs are rejected with reporting rather than silently
dropped or used as paths.

## Confirmed root cause

In `run_extract` ([extract_runner.py:393](gui/utils/extract_runner.py#L393)):

```python
dest_path = download_dir / share / file_info["local_rel_path"]
dest_path.parent.mkdir(parents=True, exist_ok=True)
```

- `share` comes from `normalized_shares`
  ([:257](gui/utils/extract_runner.py#L257)) via `share.strip("\\/ ")`, which
  strips only leading/trailing separators; interior `..`, `/`, `\` survive, so
  `download_dir / "../../etc"` normalizes upward and `mkdir`/`open` write
  outside the root.
- Verified: `_sanitize_segment("\\pub\\")` → `"pub"` while the exact server
  value is `\pub\` — the current strip already mutates the remote identity used
  by `listPath`/`getFile`/reporting (RA High, Rev 2).
- Verified: `_safe_parts("..\\..")` → `[]` → `Path(".")`, `.parts == ()` — a
  hostile filename can collapse to the share root (RA empty-path, Rev 2).
- Verified: naive sorted collision allocation maps `["../pub","pub"]` →
  `{"../pub":"pub","pub":"pub-2"}` — a hostile alias steals the normal base
  (RA Medium, Rev 3).
- The browser download path is unaffected (`build_quarantine_path` already
  sanitizes via `_sanitize_label`); out of C4 scope; unchanged.

## Non-goals

- No change to remote SMB behavior other than passing the *exact* share to
  `listPath`/`getFile`.
- No change to `shared/quarantine_promotion.py`, `shared/quarantine.py`, the
  `PromotionConfig` shape, or ClamAV enable/promote/fail-open semantics.
- No browser/FTP/HTTP extract changes. No dependency, schema, auth, CI, public
  CLI, or GUI↔CLI boundary changes.

## Exact behavior and interfaces

Two distinct identities flow through the per-file loop:

| Identity | Value | Used for |
|---|---|---|
| **`remote_share`** | exact server string (un-stripped) | `listPath`, `getFile`, `summary["shares_requested"]`, `summary["files"/"skipped"/"errors"]["share"]`, `log_quarantine_event` message, `PostProcessInput.share` |
| **`local_label`** | sanitized, unique, order-independent, single segment | building `dest_path`; `PostProcessInput.local_share` (promotion on-disk routing) |

`PostProcessInput.share` stays the exact remote identity. A **new** optional
`PostProcessInput.local_share` field carries the on-disk label. In
`build_clamav_post_processor._scan`, the share passed to `resolve_promotion_dest`
becomes `inp.local_share or inp.share` (that function uses the share to both
locate the file under `download_dir` and name the destination subtree — both
must track on-disk location). The `or inp.share` fallback keeps the browser
caller (no `local_share`) behaving exactly as today.

### Share intake: exact preservation + explicit empty rejection

Keep every provided string share (do not mutate the value); compute usable
shares only to drive the "no shares" guard and label map. Empty / separator-only
shares (those whose `strip("\\/ ")` is empty, e.g. `""`, `"   "`, `"///"`,
`"\\"`) are **rejected per-share with reporting and no SMB call**. Dot-only
shares (`"."`, `".."`) are non-empty, are **retained**, and map to the fallback
label `"share"`.

```python
str_shares = [s for s in shares if isinstance(s, str)]
usable_shares = [s for s in str_shares if s.strip("\\/ ")]
if not usable_shares:
    raise ExtractError("No accessible shares provided.")
summary["shares_requested"] = str_shares          # exact request, incl. empties
share_labels = _build_share_label_map(usable_shares)
```

In the per-share loop:

```python
for remote_share in str_shares:
    _check_cancel(cancel_event)
    if not remote_share.strip("\\/ "):
        summary["errors"].append({
            "share": remote_share,
            "message": "Rejected: empty or separator-only share name",
        })
        continue
    if _time_exceeded(...): ...
    # connect / login / walk use the exact remote_share
```

### Order-independent local-label allocation (alias cannot steal)

```python
def _build_share_label_map(usable_shares: Sequence[str]) -> Dict[str, str]:
    uniq = sorted(set(usable_shares))
    base_of = {s: _promo_sanitize_segment(s, fallback="share") for s in uniq}
    reserved = set(base_of.values())              # every natural base protected
    groups: Dict[str, List[str]] = {}
    for s in uniq:
        groups.setdefault(base_of[s], []).append(s)
    labels: Dict[str, str] = {}
    used: set = set()
    for base in sorted(groups):
        # exact safe owner (remote == base) first, then deterministic by string
        members = sorted(groups[base], key=lambda s: (s != base, s))
        for i, s in enumerate(members):
            if i == 0:
                label = base
            else:
                n = i + 1
                label = f"{base}-{n}"
                while label in reserved or label in used:
                    n += 1
                    label = f"{base}-{n}"
            labels[s] = label
            used.add(label)
    return labels
```

Properties (verified):
- `["../pub","pub"]` → `{"pub":"pub","../pub":"pub-2"}` for any input order — the
  exact owner `pub` keeps `pub`; the alias is suffixed.
- Reserve case `["../a","..\\a","a-2"]` → `{"../a":"a","..\\a":"a-3","a-2":"a-2"}`
  — a real `a-2` is never displaced; a synthesized suffix skips reserved bases.
- Permutation-invariant: all of `set`, groups, and members are sorted.

### Per-file validation (before any mutation)

```python
local_rel = file_info["local_rel_path"]
if not local_rel.parts:                           # _safe_parts collapsed to "."
    _reject(remote_share, rel_display, file_size, "empty_relative_path",
            "Rejected: file name sanitized to an empty path")
    continue
local_label = share_labels[remote_share]
dest_path = download_dir / local_label / local_rel
if not _resolved_within(dest_path, root_resolved):
    _reject(remote_share, rel_display, file_size, "path_containment",
            "Rejected: resolved local path escapes download root")
    continue
dest_path.parent.mkdir(parents=True, exist_ok=True)
```

`_resolved_within` (3.8-safe; follows existing-parent symlinks, catches
symlink-parent escape):

```python
def _resolved_within(dest: Path, root_resolved: Path) -> bool:
    try:
        dest.resolve().relative_to(root_resolved)
        return True
    except (ValueError, OSError, RuntimeError):
        return False
```

A file rejection increments `files_skipped`, appends to `summary["skipped"]`
(with `reason`) and `summary["errors"]` (exact `remote_share`), and `continue`s —
no `mkdir`, no `getFile`, no progress/download count. (`_reject` may be an inline
block or a tiny local closure.)

`root_resolved = download_dir.resolve()` and `share_labels` are computed once,
right after `download_dir.mkdir(...)`.

## File-by-file changes

### `gui/utils/extract_runner.py` (825 → ~900; excellent)

1. Replace the `normalized_shares` mutation with `str_shares` / `usable_shares`
   (exact preservation); keep the "No accessible shares" guard on
   `usable_shares`; set `summary["shares_requested"] = str_shares`; build
   `share_labels` from `usable_shares`.
2. After `download_dir.mkdir(...)`: add `root_resolved` and `share_labels`.
3. Add module-level `_build_share_label_map` (with natural-owner priority key)
   and `_resolved_within` (near `_safe_parts`).
4. Rename the loop variable `share` → `remote_share`; reject empty/
   separator-only shares at the top of the loop (no SMB call); keep all SMB
   calls (`_walk_files`, `getFile`) and reporting on the exact `remote_share`.
5. Insert empty-relative-path and containment rejection blocks before
   `dest_path.parent.mkdir(...)`; build `dest_path` from `local_label`.
6. In `build_clamav_post_processor._scan`, pass `inp.local_share or inp.share`
   to `resolve_promotion_dest`.
7. In the `PostProcessInput(...)` seam, set `share=remote_share`,
   `local_share=local_label`.
8. Leave the `summary` reporting fields, the quarantine log message, and
   `_safe_parts` otherwise intact.

### `shared/quarantine_postprocess.py` (49 → ~51)

Add an optional field to `PostProcessInput` (backward compatible; `Optional`
already imported):

```python
local_share: Optional[str] = None   # sanitized on-disk label; falls back to share for promotion
```

### `gui/tests/test_extract_runner_clamav.py` (817 → ~1080)

New C4 test group (below).

### `shared/tests/test_quarantine_postprocess.py` (186 → ~205)

`local_share` default + field-set assertions.

## Edge and failure cases

- **Exact remote share preserved**: `\pub\` (or whitespace-padded) reaches
  `listPath`/`getFile` and all `summary` share fields unchanged; file under `pub`.
- **Empty / separator-only share** (`""`, `"   "`, `"///"`, `"\\"`) → per-share
  rejection in `summary["errors"]`, no SMB call; never produces a path.
- **All-empty input** → `ExtractError("No accessible shares provided.")`.
- **Dot-only share** (`"."`, `".."`) → retained, fallback label `"share"`,
  beneath root.
- **Alias-vs-normal collision** `["../pub","pub"]` → normal `pub` keeps `pub`;
  alias gets `pub-2`; order-independent.
- **Reserve collision** → a real `a-2` is never renamed; synthesized labels skip
  reserved bases.
- **Traversal / slash / backslash / absolute share** → sanitized single label;
  contained.
- **Symlink-parent escape** → rejected before `mkdir`/`open`; `getFile` not
  called; outside target absent.
- **Empty sanitized filename** (`..\..`) → rejected (`empty_relative_path`); no
  SMB download; share root never used as a destination.
- **Hostile relative filename** (`..\x\y` → `x/y`) → contained under the label.
- **Normal share** `pub` → `pub`; layout, promotion destinations, existing tests
  unchanged.
- **Rejection accounting**: rejected files increment `files_skipped` only, are
  absent from `summary["files"]`, and do not advance the download/progress count.

## Tests and exact commands

Add to `gui/tests/test_extract_runner_clamav.py` (extend existing helpers; add a
call-recording fake conn capturing `listPath`/`getFile` share args):

1. `test_c4_exact_remote_share_preserved_through_smb_and_reporting` — `\pub\`
   reaches `listPath`/`getFile` exactly and appears in `shares_requested` /
   `files[0]["share"]`; file on disk under `pub`.
2. `test_c4_postprocessor_receives_both_identities` (RA Low) — custom
   `post_processor` captures the `PostProcessInput`; assert `inp.share` is the
   exact hostile remote string and `inp.local_share` is the sanitized label.
3. `test_c4_traversal_share_contained` — `../../etc` → `download_dir/etc/a.txt`.
4. `test_c4_slash_and_backslash_share_contained`.
5. `test_c4_absolute_share_contained` — `\\evil\share` → one segment.
6. `test_c4_empty_share_rejected` — `""` → error entry, no `getFile`/`listPath`.
7. `test_c4_separator_only_share_rejected` — `"///"` → error entry, no SMB call.
8. `test_c4_all_empty_shares_raise` — `["", "   "]` → `ExtractError`.
9. `test_c4_dot_only_share_uses_fallback_label` — `"."` → label `share`.
10. `test_c4_symlink_parent_escape_rejected` — symlinked label dir → rejected,
    `getFile` not called, outside target absent, `summary["files"]` empty.
11. `test_c4_empty_relative_filename_rejected` — server name `..\..` → rejected
    (`empty_relative_path`), `getFile` not called, no file written.
12. `test_c4_hostile_relative_filename_sanitized` — `..\x\y` → under label.
13. `test_c4_normal_share_layout_unchanged` — `pub` → `download_dir/pub/a.txt`.
14. `test_c4_hostile_share_promotes_under_local_label` (ClamAV clean) — promotion
    under `extracted/<host>/<date>/<safe_label>/a.txt`, `promoted == 1`,
    `errors == 0`.
15. `test_c4_label_map_alias_does_not_steal_normal_base` — `["../pub","pub"]` and
    its reverse both yield `pub→pub`, `../pub→pub-2`.
16. `test_c4_label_map_permutation_invariant` — fixed expected mapping holds for
    every `itertools.permutations` of a multi-collision input including a real
    `a-2` (asserts `a-2` kept, synthesized `a-3`).

Add to `shared/tests/test_quarantine_postprocess.py`:

17. `test_postprocess_input_local_share_defaults_none` and a `local_share`
    field-set assertion.

Validation commands (card-declared):

```bash
./venv/bin/python -m pytest \
  gui/tests/test_extract_runner_clamav.py \
  shared/tests/test_quarantine_postprocess.py -q
./venv/bin/python -m py_compile gui/utils/extract_runner.py shared/quarantine_postprocess.py
git diff --check
```

Expected: prior 41 still pass + new C4 tests pass; clean compile; no whitespace
errors.

## Line-count risk

- `extract_runner.py`: 825 → ~900 (excellent).
- `quarantine_postprocess.py`: 49 → ~51.
- `test_extract_runner_clamav.py`: 817 → ~1080.
- `test_quarantine_postprocess.py`: 186 → ~205.
- No file crosses 1700; no modularization gate triggered.

## Rollback

Two product files. Revert `gui/utils/extract_runner.py` (helpers, exact-share
intake, empty-share reject, `root_resolved`/`share_labels`, file rejection
blocks, `_scan` and `PostProcessInput` seam, loop rename) and the optional
`local_share` field in `shared/quarantine_postprocess.py`; drop the new tests.
No data, schema, or config migration. The `local_share` default keeps all other
`PostProcessInput` callers source-compatible.

## Residual notes

- Passing the exact (un-stripped) remote share to `listPath`/`getFile` is
  card-required; upstream callers already strip share strings, so normal SMB
  behavior is unchanged.
- The browser download promotion path retains pre-existing behavior
  (`local_share` unset → falls back to `share`); out of C4 scope, unchanged.

## DA handoff prompt

> You are Claude (DA) implementing **C4 — SMB Extract Path Containment** on
> branch `development` at `8a3debc`. Read AGENTS.md, CLAUDE.md, the
> `docs/dev/security_review_10JUN26/` SOP/SPEC/TASK_CARDS/VALIDATION_PLAN/
> RISK_REGISTER/LESSONS_LEARNED, and the approved C4 Rev 3 plan. Confirm clean
> worktree and current line counts first.
>
> `gui/utils/extract_runner.py`:
> 1. Share intake: keep exact string shares; `usable_shares` = those with
>    non-empty `strip("\\/ ")`; raise `ExtractError("No accessible shares
>    provided.")` only when none are usable; `summary["shares_requested"]` =
>    exact str shares; build `share_labels` from `usable_shares`.
> 2. Add `_build_share_label_map` — order-independent: `sorted(set(...))`,
>    reserve all natural bases, members sorted by `(s != base, s)` so the exact
>    safe owner keeps the base, suffix `base-N` skipping reserved/used — and
>    `_resolved_within` (3.8-safe `resolve().relative_to`, except
>    ValueError/OSError/RuntimeError).
> 3. After `download_dir.mkdir(...)`, compute `root_resolved` and `share_labels`.
> 4. Rename loop var `share` → `remote_share`; at loop top, reject
>    empty/separator-only shares into `summary["errors"]` with no SMB call; keep
>    all SMB calls and reporting on the exact `remote_share`.
> 5. Before `dest_path.parent.mkdir(...)`: reject empty `local_rel.parts`
>    (`empty_relative_path`) and non-contained `dest_path` (`path_containment`),
>    recording skip+error and `continue` (no mkdir/open/getFile/count). Build
>    `dest_path = download_dir / local_label / local_rel`.
> 6. In `build_clamav_post_processor._scan`, pass `inp.local_share or inp.share`
>    to `resolve_promotion_dest`.
> 7. In the `PostProcessInput(...)` seam, set `share=remote_share`,
>    `local_share=local_label`.
>
> `shared/quarantine_postprocess.py`: add `local_share: Optional[str] = None` to
> `PostProcessInput`.
>
> Add the 17 tests from the plan. Run the declared validation commands,
> `py_compile`, and `git diff --check`. Do not commit or push; report using
> `SOP_CONSTRAINTS.md` format and stop for RA review.
