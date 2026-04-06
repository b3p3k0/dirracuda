"""
Unit tests for redseek/service.py.

Covers: new cursor semantics, top dedupe, replace_cache, all error paths,
subreddit lock, and IngestResult field consistency.

All network calls are mocked. DB operations use tmp_path for isolation.
No conftest.py — all fixtures and helpers are local.
"""

import datetime
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from experimental.redseek.client import FetchError, FetchResult, RateLimitError
from experimental.redseek.models import RedditIngestState
from experimental.redseek.store import (
    get_ingest_state,
    init_db,
    open_connection,
    save_ingest_state,
)
from experimental.redseek.service import IngestOptions, IngestResult, run_ingest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _make_raw_post(
    post_id: str,
    created_utc: float = 1_700_000_000.0,
    title: str = "No URL in this title",
    author: str = "alice",
    selftext: str = "",
    over_18: bool = False,
) -> dict:
    return {
        "id": post_id,
        "created_utc": created_utc,
        "title": title,
        "author": author,
        "selftext": selftext,
        "over_18": over_18,
    }


def _make_fetch(posts: list, pages_fetched: int = 1) -> FetchResult:
    return FetchResult(posts=posts, pages_fetched=pages_fetched)


def _make_opts(
    sort: str = "new",
    max_posts: int = 50,
    parse_body: bool = False,
    include_nsfw: bool = True,
    replace_cache: bool = False,
    max_pages: int = 1,
) -> IngestOptions:
    return IngestOptions(
        sort=sort,
        max_posts=max_posts,
        parse_body=parse_body,
        include_nsfw=include_nsfw,
        replace_cache=replace_cache,
        max_pages=max_pages,
    )


def _set_cursor(db: Path, sort_mode: str, utc: float, post_id: str) -> None:
    with open_connection(db) as conn:
        save_ingest_state(conn, RedditIngestState(
            subreddit="opendirectories",
            sort_mode=sort_mode,
            last_post_created_utc=utc,
            last_post_id=post_id,
            last_scrape_time=_NOW,
        ))
        conn.commit()


def _row_counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    result = {
        "posts": conn.execute("SELECT COUNT(*) FROM reddit_posts").fetchone()[0],
        "targets": conn.execute("SELECT COUNT(*) FROM reddit_targets").fetchone()[0],
        "state": conn.execute("SELECT COUNT(*) FROM reddit_ingest_state").fetchone()[0],
    }
    conn.close()
    return result


# ---------------------------------------------------------------------------
# new mode — cursor semantics
# ---------------------------------------------------------------------------

def test_new_first_run_no_cursor(tmp_path):
    db = tmp_path / "test.db"
    posts = [
        _make_raw_post("p2", created_utc=1_700_000_002.0),
        _make_raw_post("p1", created_utc=1_700_000_001.0),
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is None
    assert result.posts_stored == 2
    assert result.stopped_by_cursor is False

    with open_connection(db) as conn:
        state = get_ingest_state(conn, "opendirectories", "new")
    assert state is not None
    assert state.last_post_id == "p2"
    assert state.last_post_created_utc == pytest.approx(1_700_000_002.0)


def test_new_cursor_stops_early(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    _set_cursor(db, "new", utc=1_700_000_001.0, post_id="p1")

    posts = [
        _make_raw_post("p3", created_utc=1_700_000_003.0),  # newer → write
        _make_raw_post("p1", created_utc=1_700_000_001.0),  # == cursor → stop
        _make_raw_post("p0", created_utc=1_700_000_000.0),  # older → never reached
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.stopped_by_cursor is True
    assert result.posts_stored == 1

    with open_connection(db) as conn:
        state = get_ingest_state(conn, "opendirectories", "new")
    assert state.last_post_id == "p3"  # cursor advanced to newest new post


def test_new_tie_break_same_utc(tmp_path):
    """Posts with same created_utc: post_id > cursor_post_id is written; <= stops."""
    db = tmp_path / "test.db"
    init_db(db)
    _set_cursor(db, "new", utc=1_700_000_000.0, post_id="p1")

    posts = [
        _make_raw_post("p2", created_utc=1_700_000_000.0),  # "p2" > "p1" → write
        _make_raw_post("p0", created_utc=1_700_000_000.0),  # "p0" < "p1" → stop
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.posts_stored == 1
    assert result.stopped_by_cursor is True

    conn = sqlite3.connect(str(db))
    ids = [r[0] for r in conn.execute("SELECT post_id FROM reddit_posts").fetchall()]
    conn.close()
    assert "p2" in ids
    assert "p0" not in ids


def test_new_max_posts_cap(tmp_path):
    db = tmp_path / "test.db"
    posts = [
        _make_raw_post("p3", created_utc=1_700_000_003.0),
        _make_raw_post("p2", created_utc=1_700_000_002.0),
        _make_raw_post("p1", created_utc=1_700_000_001.0),
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new", max_posts=1), db_path=db)

    assert result.stopped_by_max_posts is True
    assert result.posts_stored == 1


def test_new_nsfw_filtered_cursor_advances(tmp_path):
    """NSFW post not written but cursor still advances past it."""
    db = tmp_path / "test.db"
    posts = [
        _make_raw_post("nsfw1", created_utc=1_700_000_002.0, over_18=True),
        _make_raw_post("safe1", created_utc=1_700_000_001.0),
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new", include_nsfw=False), db_path=db)

    assert result.posts_stored == 1
    assert result.posts_skipped == 1

    conn = sqlite3.connect(str(db))
    ids = [r[0] for r in conn.execute("SELECT post_id FROM reddit_posts").fetchall()]
    conn.close()
    assert "nsfw1" not in ids
    assert "safe1" in ids

    # Cursor advanced past the NSFW post — next run won't re-fetch it
    with open_connection(db) as conn:
        state = get_ingest_state(conn, "opendirectories", "new")
    assert state.last_post_id == "nsfw1"
    assert state.last_post_created_utc == pytest.approx(1_700_000_002.0)


def test_new_nsfw_included(tmp_path):
    db = tmp_path / "test.db"
    posts = [_make_raw_post("nsfw1", created_utc=1_700_000_000.0, over_18=True)]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new", include_nsfw=True), db_path=db)

    assert result.posts_stored == 1

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT is_nsfw FROM reddit_posts WHERE post_id=?", ("nsfw1",)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 1


def test_new_cursor_not_written_on_empty_run(tmp_path):
    """All fetched posts are <= cursor: cursor row must not change."""
    db = tmp_path / "test.db"
    init_db(db)
    _set_cursor(db, "new", utc=1_700_000_002.0, post_id="p2")

    posts = [_make_raw_post("p1", created_utc=1_700_000_001.0)]  # < cursor → stop
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.stopped_by_cursor is True
    assert result.posts_stored == 0

    with open_connection(db) as conn:
        state = get_ingest_state(conn, "opendirectories", "new")
    assert state.last_post_id == "p2"                             # unchanged
    assert state.last_post_created_utc == pytest.approx(1_700_000_002.0)


def test_new_cursor_null_fields_not_applied(tmp_path):
    """State row with None cursor fields must not trigger cursor stop."""
    db = tmp_path / "test.db"
    init_db(db)
    with open_connection(db) as conn:
        save_ingest_state(conn, RedditIngestState(
            subreddit="opendirectories",
            sort_mode="new",
            last_post_created_utc=None,
            last_post_id=None,
            last_scrape_time=_NOW,
        ))
        conn.commit()

    posts = [_make_raw_post("p1", created_utc=1_700_000_000.0)]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is None
    assert result.posts_stored == 1
    assert result.stopped_by_cursor is False


def test_new_parse_anomaly_does_not_count_toward_max_posts(tmp_path):
    """Parse-anomaly discards do not consume the max_posts budget."""
    db = tmp_path / "test.db"
    posts = [
        {"title": "anomaly — no id field", "created_utc": 1_700_000_002.0},
        _make_raw_post("p2", created_utc=1_700_000_001.0),
        _make_raw_post("p1", created_utc=1_700_000_000.0),
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="new", max_posts=1), db_path=db)

    # p2 consumes the one valid slot; p1 triggers the cap; anomaly did not count
    assert result.posts_stored == 1
    assert result.posts_skipped == 1      # anomaly only
    assert result.stopped_by_max_posts is True


def test_new_parser_exception_stores_post_had_targets_0(tmp_path):
    """On parser exception: post stored with had_targets=0; run continues."""
    db = tmp_path / "test.db"
    posts = [
        _make_raw_post("p1", title="http://will-raise.com"),
        _make_raw_post("p2", title="No URL"),
    ]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        with patch(
            "experimental.redseek.service.extract_targets",
            side_effect=[Exception("parse boom"), []],
        ):
            result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.parse_errors == 1
    assert result.posts_stored == 2   # both posts written despite exception on p1
    assert result.error is None

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT had_targets FROM reddit_posts WHERE post_id=?", ("p1",)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == 0


# ---------------------------------------------------------------------------
# top mode — dedupe and state
# ---------------------------------------------------------------------------

def test_top_dedupe_repeat_run(tmp_path):
    """Same posts ingested twice produce no duplicate rows."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1"), _make_raw_post("p2")]
    fetch = _make_fetch(posts)

    with patch("experimental.redseek.service.fetch_posts", return_value=fetch):
        r1 = run_ingest(_make_opts(sort="top"), db_path=db)
    with patch("experimental.redseek.service.fetch_posts", return_value=fetch):
        r2 = run_ingest(_make_opts(sort="top"), db_path=db)

    assert r1.error is None
    assert r2.error is None
    assert _row_counts(db)["posts"] == 2


def test_top_targets_deduped_counted(tmp_path):
    """Second run over same posts: targets_deduped > 0, targets_stored == 0."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1", title="http://example.com")]
    fetch = _make_fetch(posts)
    opts = _make_opts(sort="top", parse_body=False)

    with patch("experimental.redseek.service.fetch_posts", return_value=fetch):
        r1 = run_ingest(opts, db_path=db)
    with patch("experimental.redseek.service.fetch_posts", return_value=fetch):
        r2 = run_ingest(opts, db_path=db)

    assert r1.targets_stored > 0
    assert r2.targets_deduped > 0
    assert r2.targets_stored == 0


def test_top_updates_scrape_time_only(tmp_path):
    """top mode saves last_scrape_time; cursor fields remain None."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1")]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        result = run_ingest(_make_opts(sort="top"), db_path=db)

    assert result.error is None

    with open_connection(db) as conn:
        state = get_ingest_state(conn, "opendirectories", "top")

    assert state is not None
    assert state.last_scrape_time is not None
    assert state.last_post_created_utc is None
    assert state.last_post_id is None


# ---------------------------------------------------------------------------
# replace_cache
# ---------------------------------------------------------------------------

def test_replace_cache_wipes_before_run(tmp_path):
    db = tmp_path / "test.db"

    # Populate with old data
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch([_make_raw_post("old1")])):
        run_ingest(_make_opts(sort="new"), db_path=db)
    assert _row_counts(db)["posts"] == 1

    # Replace cache + new post
    new_posts = [_make_raw_post("new1", created_utc=1_700_000_999.0)]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(new_posts)):
        result = run_ingest(_make_opts(sort="new", replace_cache=True), db_path=db)

    assert result.replace_cache_done is True
    assert result.posts_stored == 1

    conn = sqlite3.connect(str(db))
    ids = [r[0] for r in conn.execute("SELECT post_id FROM reddit_posts").fetchall()]
    conn.close()
    assert "old1" not in ids
    assert "new1" in ids


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_rate_limit_error_returns_clean_result(tmp_path):
    db = tmp_path / "test.db"
    with patch("experimental.redseek.service.fetch_posts", side_effect=RateLimitError("429")):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.rate_limited is True
    assert result.error == "HTTP 429"
    assert result.posts_stored == 0


def test_fetch_error_returns_clean_result(tmp_path):
    db = tmp_path / "test.db"
    with patch("experimental.redseek.service.fetch_posts", side_effect=FetchError("connection reset")):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is not None
    assert result.rate_limited is False
    assert result.posts_stored == 0


def test_schema_drift_returns_error_result(tmp_path):
    """RuntimeError from open_connection (schema drift) returns clean error result."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1")]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        with patch(
            "experimental.redseek.service.open_connection",
            side_effect=RuntimeError("schema mismatch"),
        ):
            result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is not None
    assert result.replace_cache_done is False
    assert result.posts_stored == 0


def test_open_connection_sqlite_error_returns_error_result(tmp_path):
    """sqlite3.Error from open_connection (permissions/lock) returns clean error result."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1")]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts)):
        with patch(
            "experimental.redseek.service.open_connection",
            side_effect=sqlite3.OperationalError("unable to open database"),
        ):
            result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is not None
    assert result.posts_stored == 0


def test_replace_cache_done_propagated_on_network_error(tmp_path):
    """replace_cache wipes DB before fetch; if fetch fails DB stays wiped — by design."""
    db = tmp_path / "test.db"

    # Pre-populate
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch([_make_raw_post("old1")])):
        run_ingest(_make_opts(sort="new"), db_path=db)

    # replace_cache=True + fetch failure
    with patch("experimental.redseek.service.fetch_posts", side_effect=FetchError("timeout")):
        result = run_ingest(_make_opts(sort="new", replace_cache=True), db_path=db)

    assert result.replace_cache_done is True
    assert result.error is not None
    assert _row_counts(db)["posts"] == 0  # DB wiped and not repopulated — by design


def test_setup_phase_db_error_returns_clean_result(tmp_path):
    db = tmp_path / "test.db"
    with patch("experimental.redseek.service.init_db", side_effect=sqlite3.Error("disk full")):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is not None
    assert result.posts_stored == 0
    assert result.replace_cache_done is False


def test_unsupported_subreddit_returns_error(tmp_path):
    """Unsupported subreddit returns error before any IO."""
    db = tmp_path / "test.db"
    opts = IngestOptions(
        sort="new",
        max_posts=50,
        parse_body=False,
        include_nsfw=True,
        replace_cache=False,
        subreddit="pics",
    )
    result = run_ingest(opts, db_path=db)

    assert result.error is not None
    assert "pics" in result.error
    assert result.posts_stored == 0
    assert not db.exists()  # validation fires before init_db creates the file


# ---------------------------------------------------------------------------
# Smoke: IngestResult field consistency
# ---------------------------------------------------------------------------

def test_ingest_result_fields_populated(tmp_path):
    """All IngestResult fields have correct types after a clean run."""
    db = tmp_path / "test.db"
    posts = [_make_raw_post("p1", title="http://example.com")]
    with patch("experimental.redseek.service.fetch_posts", return_value=_make_fetch(posts, pages_fetched=1)):
        result = run_ingest(_make_opts(sort="new"), db_path=db)

    assert result.error is None
    assert isinstance(result.sort, str)
    assert isinstance(result.subreddit, str)
    assert isinstance(result.pages_fetched, int)
    assert isinstance(result.posts_stored, int)
    assert isinstance(result.posts_skipped, int)
    assert isinstance(result.targets_stored, int)
    assert isinstance(result.targets_deduped, int)
    assert isinstance(result.parse_errors, int)
    assert isinstance(result.stopped_by_cursor, bool)
    assert isinstance(result.stopped_by_max_posts, bool)
    assert isinstance(result.replace_cache_done, bool)
    assert isinstance(result.rate_limited, bool)
    assert result.pages_fetched == 1
