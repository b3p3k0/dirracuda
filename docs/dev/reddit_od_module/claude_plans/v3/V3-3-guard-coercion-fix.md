# Plan: V3-3 Guard Coercion Fix

## Context

`_run_user()` runtime guards call `.lower()` directly on the result of `raw.get(...)`. If the
Reddit payload contains `subreddit: null` or `author: null` (or any non-string), `raw.get`
returns `None` and `.lower()` raises `AttributeError`. The `run_ingest` docstring guarantees
it never raises — violation of contract.

## Fix

Two-line surgical change in [experimental/redseek/service.py](experimental/redseek/service.py),
lines 594 and 597:

```python
# Before
if raw.get("subreddit", "").lower() != "opendirectories":
if raw.get("author", "").lower() != _uname.lower():

# After
if str(raw.get("subreddit") or "").lower() != "opendirectories":
if str(raw.get("author") or "").lower() != _uname.lower():
```

`or ""` converts `None` (and any other falsy value) to `""` before `str()`, so `.lower()` is
always called on a string. A `None`/non-string field produces `""`, which never matches
`"opendirectories"` or the requested username, so the post is correctly skipped.

## Test

One new test in [shared/tests/test_redseek_service.py](shared/tests/test_redseek_service.py):

```
test_user_mode_nonstring_subreddit_and_author_does_not_raise
```

Two posts in the fetch result to exercise both falsy and truthy non-string cases:
- Post 1: `subreddit=None`, `author=None` (falsy non-string)
- Post 2: `subreddit=123`, `author=456` (truthy non-string)

For each post, calls `run_ingest(mode="user", username="testuser")` (run once with both posts).
Asserts:
- Result is an `IngestResult` (no exception raised)
- `posts_stored == 0`
- `posts_skipped == 2` (one per bad post, deterministic)
- `error is None`

## Validation

```bash
# 1. Syntax check
python3 -m py_compile experimental/redseek/service.py

# 2. New test only
./venv/bin/python -m pytest \
  shared/tests/test_redseek_service.py \
  -v -k "nonstring_subreddit"

# 3. Full regression
./venv/bin/python -m pytest \
  shared/tests/test_redseek_client.py \
  shared/tests/test_redseek_service.py \
  gui/tests/test_reddit_grab_dialog.py -q

# 4. Line-count check (rubric: <=1200 excellent)
wc -l experimental/redseek/service.py shared/tests/test_redseek_service.py
```

Expected: new test PASS, 133 existing tests PASS, both files well under 1200 lines.
