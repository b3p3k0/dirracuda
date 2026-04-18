# C1 — Replace Placeholder With SearXNG Dorking Tab Scaffold

## Context

The Experimental Features dialog currently shows two tabs: `Reddit` and `placeholder`. C1 replaces the `placeholder` tab with a non-functional `SearXNG Dorking` tab shell — the first step toward the full SearXNG dork search pipeline defined in SPEC.md. No network logic is introduced here; all buttons are stubs.

---

## Files Modified

| File | Action | Current lines |
|------|--------|--------------|
| `gui/components/experimental_features/registry.py` | Edit — swap placeholder for se_dork | 52 |
| `gui/components/experimental_features/se_dork_tab.py` | Create | 0 → ~80 |
| `gui/tests/test_experimental_features_dialog.py` | Edit — add tab-label tests | 364 |
| `gui/components/experimental_features/placeholder_tab.py` | Delete (after grep gate) | 47 |
| `experimental/placeholder/__init__.py` | Delete (after grep gate) | 13 |

All well under 1200 lines — excellent rating.

---

## Implementation Steps

### Step 1 — Edit `registry.py`

Remove the placeholder import and entry. Add the se_dork import and entry:

```python
def _get_features() -> List[ExperimentalFeature]:
    from gui.components.experimental_features.reddit_tab import build_reddit_tab
    from gui.components.experimental_features.se_dork_tab import build_se_dork_tab
    return [
        ExperimentalFeature(feature_id="reddit",   label="Reddit",          build_tab=build_reddit_tab),
        ExperimentalFeature(feature_id="se_dork",  label="SearXNG Dorking", build_tab=build_se_dork_tab),
    ]
```

### Step 2 — Create `se_dork_tab.py`

Follow the `RedditTab` class pattern. UI layout (all non-functional):

- Description label: "SearXNG-driven dork search. Run open-directory queries against a local SearXNG instance."
- **Instance URL**: `tk.Label` + `tk.Entry` (default `http://192.168.1.20:8090`)
- **Query**: `tk.Label` + `tk.Entry` (default `site:* intitle:"index of /"`)
- **Buttons row**: `Test` (button_primary) | `Run` (button_primary) | `Open Results DB` (button_secondary) — all `command=lambda: None`
- **Status label**: empty `tk.Label`, themed as `label`

Factory function: `build_se_dork_tab(parent, context) -> tk.Widget`

### Step 3 — Run grep gate

```bash
rg -rn "placeholder_tab|from.*placeholder|import.*placeholder" gui/ experimental/ --glob '*.py'
```

Expected: zero matches (both files are no longer imported anywhere after Step 1).

### Step 4 — Delete obsolete files (only if grep gate passes)

```
gui/components/experimental_features/placeholder_tab.py
experimental/placeholder/__init__.py
```

### Step 5 — Update tests in `test_experimental_features_dialog.py`

Add a new section at the bottom: **C1 — Tab registry assertions**

Four focused tests, no tkinter construction needed (they only call `_get_features()` which returns dataclasses):

```python
def test_registry_contains_searxng_dorking_tab():
    labels = [f.label for f in _get_features()]
    assert "SearXNG Dorking" in labels

def test_registry_does_not_contain_placeholder_tab():
    labels = [f.label for f in _get_features()]
    assert "placeholder" not in labels

def test_registry_reddit_tab_unchanged():
    labels = [f.label for f in _get_features()]
    assert "Reddit" in labels

def test_registry_se_dork_feature_id():
    ids = [f.feature_id for f in _get_features()]
    assert "se_dork" in ids
```

---

## Validation Commands (run exactly)

```bash
# Line counts before/after
wc -l \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/se_dork_tab.py \
  gui/tests/test_experimental_features_dialog.py

# Compile check
python3 -m py_compile \
  gui/components/experimental_features/registry.py \
  gui/components/experimental_features/se_dork_tab.py

# Test suite
./venv/bin/python -m pytest \
  gui/tests/test_experimental_features_dialog.py \
  gui/tests/test_dashboard_reddit_wiring.py -q
```

---

## Risks & Constraints

- **Grep gate is mandatory** before deleting placeholder files — if any live ref remains, keep the files and flag it.
- Reddit tab behavior must be unchanged; `test_dashboard_reddit_wiring.py` is the regression guard.
- Buttons in se_dork_tab are `command=lambda: None` stubs — no network calls, no context keys consumed.
- `experimental/se_dork/` directory (for C2+ network logic) is **not** created here.
- No `__init__.py` changes needed — the lazy import in `_get_features()` handles discovery.

---

## HI Manual Check

After implementation:
1. Launch: `./dirracuda`
2. Dashboard → Experimental button → Experimental Features dialog
3. Confirm: two tabs — `Reddit` and `SearXNG Dorking` (no `placeholder` tab)
4. Click `SearXNG Dorking` tab — Instance URL field, Query field, three buttons, status area visible
5. Click Reddit tab — confirm existing Reddit Grab / Open Reddit Post DB buttons still work
