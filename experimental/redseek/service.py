"""
Ingestion orchestration for the redseek module.

Entry point:
    run_ingest(options: IngestOptions, db_path=None) -> IngestResult

Transaction ownership:
    - wipe_all() and init_db() own their connections (called in run_ingest).
    - _run_new / _run_top open a single write connection via open_connection(),
      commit all writes atomically at end of loop, and close on exit.
    - Network errors (RateLimitError, FetchError) are caught before any
      write connection is opened.
    - open_connection() failures (RuntimeError, FileNotFoundError, sqlite3.Error)
      are caught before the write-phase try/except and return a clean error result.

replace_cache semantics:
    If replace_cache=True, wipe_all() runs and commits before any fetch.
    If the fetch or write phase later fails the DB remains wiped — by design.
    IngestResult.replace_cache_done=True signals this state to the caller.
"""

import datetime
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from experimental.redseek.client import FetchError, FetchResult, RateLimitError, fetch_posts
from experimental.redseek.models import RedditIngestState, RedditPost
from experimental.redseek.parser import extract_targets
from experimental.redseek.store import (
    get_ingest_state,
    init_db,
    open_connection,
    save_ingest_state,
    upsert_post,
    upsert_targets,
    wipe_all,
)


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------

@dataclass
class IngestOptions:
    sort: str                    # "new" | "top"
    max_posts: int               # hard loop bound on valid posts visited (1–200)
    parse_body: bool
    include_nsfw: bool
    replace_cache: bool
    max_pages: int = 3           # 1–3, passed to fetch_posts
    subreddit: str = "opendirectories"  # locked in Card 3; client URL is hardcoded


@dataclass
class IngestResult:
    sort: str
    subreddit: str
    pages_fetched: int
    posts_stored: int
    posts_skipped: int        # NSFW-filtered or parse-anomaly discards
    targets_stored: int
    targets_deduped: int      # INSERT OR IGNORE skips
    parse_errors: int         # posts stored with had_targets=0 due to parser exception
    stopped_by_cursor: bool   # new mode: early stop fired
    stopped_by_max_posts: bool
    replace_cache_done: bool  # True if wipe_all ran this cycle (even if fetch later failed)
    rate_limited: bool
    error: Optional[str]      # None on success


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _error_result(options: IngestOptions, replace_cache_done: bool, **kwargs) -> IngestResult:
    """
    Construct a zero-count IngestResult for early-return error paths.

    Callers pass only the fields that differ from zero/False defaults
    (e.g., error="...", rate_limited=True). Centralising defaults here
    prevents field-count drift when IngestResult grows.
    """
    defaults: dict = dict(
        sort=options.sort,
        subreddit=options.subreddit,
        pages_fetched=0,
        posts_stored=0,
        posts_skipped=0,
        targets_stored=0,
        targets_deduped=0,
        parse_errors=0,
        stopped_by_cursor=False,
        stopped_by_max_posts=False,
        replace_cache_done=replace_cache_done,
        rate_limited=False,
        error=None,
    )
    defaults.update(kwargs)
    return IngestResult(**defaults)


def _extract_post_meta(raw: dict) -> Optional[dict]:
    """
    Extract and lightly validate fields from a raw Reddit post dict.

    Returns None (skip this post) if id or created_utc are absent or invalid.
    All other fields have safe defaults.
    """
    post_id = raw.get("id")
    if not post_id:
        return None
    raw_utc = raw.get("created_utc")
    if raw_utc is None:
        return None
    try:
        created_utc = float(raw_utc)
    except (TypeError, ValueError):
        return None
    return {
        "id": post_id,
        "created_utc": created_utc,
        "title": raw.get("title", ""),
        "author": raw.get("author"),
        "selftext": raw.get("selftext"),
        "is_nsfw": int(bool(raw.get("over_18", False))),
    }


# ---------------------------------------------------------------------------
# Mode workers
# ---------------------------------------------------------------------------

def _run_new(
    options: IngestOptions,
    fetch_result: FetchResult,
    db_path: Optional[Path],
    now_str: str,
    replace_cache_done: bool,
) -> IngestResult:
    try:
        conn = open_connection(db_path)
    except (RuntimeError, FileNotFoundError, sqlite3.Error) as e:
        return _error_result(options, replace_cache_done, error=str(e))

    try:
        state = get_ingest_state(conn, options.subreddit, "new")
        cursor_candidate = None  # (created_utc, post_id) of newest valid post visited
        posts_visited = 0
        stopped_by_cursor = False
        stopped_by_max_posts = False
        posts_stored = 0
        posts_skipped = 0
        targets_stored = 0
        targets_deduped = 0
        parse_errors = 0

        for raw in fetch_result.posts:
            meta = _extract_post_meta(raw)
            if meta is None:
                posts_skipped += 1
                continue  # anomaly: does NOT count toward max_posts

            post_utc: float = meta["created_utc"]
            post_id: str = meta["id"]

            # Hard loop bound — checked first; counts valid-meta posts
            if posts_visited >= options.max_posts:
                stopped_by_max_posts = True
                break

            # Cursor stop — only when both cursor fields are non-None
            if (
                state is not None
                and state.last_post_created_utc is not None
                and state.last_post_id is not None
                and (post_utc, post_id) <= (state.last_post_created_utc, state.last_post_id)
            ):
                stopped_by_cursor = True
                break

            posts_visited += 1

            # Advance cursor candidate (NSFW-filtered posts still advance it)
            if cursor_candidate is None or (post_utc, post_id) > cursor_candidate:
                cursor_candidate = (post_utc, post_id)

            # NSFW filter — after cursor update, before write
            if not options.include_nsfw and meta["is_nsfw"]:
                posts_skipped += 1
                continue

            # Parse targets; on exception store post with had_targets=0
            try:
                targets = extract_targets(
                    post_id,
                    meta["title"],
                    meta["selftext"],
                    options.parse_body,
                    now_str,
                )
            except Exception:
                targets = []
                parse_errors += 1

            post_obj = RedditPost(
                post_id=post_id,
                post_title=meta["title"],
                post_author=meta["author"],
                post_created_utc=post_utc,
                is_nsfw=meta["is_nsfw"],
                had_targets=1 if targets else 0,
                source_sort="new",
                last_seen_at=now_str,
            )
            upsert_post(conn, post_obj)
            before = conn.total_changes
            upsert_targets(conn, targets)
            actually_inserted = conn.total_changes - before
            targets_stored += actually_inserted
            targets_deduped += len(targets) - actually_inserted
            posts_stored += 1

        if cursor_candidate is not None:
            save_ingest_state(
                conn,
                RedditIngestState(
                    subreddit=options.subreddit,
                    sort_mode="new",
                    last_post_created_utc=cursor_candidate[0],
                    last_post_id=cursor_candidate[1],
                    last_scrape_time=now_str,
                ),
            )

        conn.commit()
        return IngestResult(
            sort="new",
            subreddit=options.subreddit,
            pages_fetched=fetch_result.pages_fetched,
            posts_stored=posts_stored,
            posts_skipped=posts_skipped,
            targets_stored=targets_stored,
            targets_deduped=targets_deduped,
            parse_errors=parse_errors,
            stopped_by_cursor=stopped_by_cursor,
            stopped_by_max_posts=stopped_by_max_posts,
            replace_cache_done=replace_cache_done,
            rate_limited=False,
            error=None,
        )
    except sqlite3.Error as e:
        conn.rollback()
        return _error_result(
            options,
            replace_cache_done,
            pages_fetched=fetch_result.pages_fetched,
            error=str(e),
        )
    finally:
        conn.close()


def _run_top(
    options: IngestOptions,
    fetch_result: FetchResult,
    db_path: Optional[Path],
    now_str: str,
    replace_cache_done: bool,
) -> IngestResult:
    try:
        conn = open_connection(db_path)
    except (RuntimeError, FileNotFoundError, sqlite3.Error) as e:
        return _error_result(options, replace_cache_done, error=str(e))

    try:
        posts_visited = 0
        stopped_by_max_posts = False
        posts_stored = 0
        posts_skipped = 0
        targets_stored = 0
        targets_deduped = 0
        parse_errors = 0

        for raw in fetch_result.posts:
            meta = _extract_post_meta(raw)
            if meta is None:
                posts_skipped += 1
                continue  # anomaly: does NOT count toward max_posts

            # Hard loop bound — checked first; counts valid-meta posts
            if posts_visited >= options.max_posts:
                stopped_by_max_posts = True
                break

            posts_visited += 1

            # NSFW filter
            if not options.include_nsfw and meta["is_nsfw"]:
                posts_skipped += 1
                continue

            # Parse targets; on exception store post with had_targets=0
            try:
                targets = extract_targets(
                    meta["id"],
                    meta["title"],
                    meta["selftext"],
                    options.parse_body,
                    now_str,
                )
            except Exception:
                targets = []
                parse_errors += 1

            post_obj = RedditPost(
                post_id=meta["id"],
                post_title=meta["title"],
                post_author=meta["author"],
                post_created_utc=meta["created_utc"],
                is_nsfw=meta["is_nsfw"],
                had_targets=1 if targets else 0,
                source_sort="top",
                last_seen_at=now_str,
            )
            upsert_post(conn, post_obj)
            before = conn.total_changes
            upsert_targets(conn, targets)
            actually_inserted = conn.total_changes - before
            targets_stored += actually_inserted
            targets_deduped += len(targets) - actually_inserted
            posts_stored += 1

        # top mode: persist scrape time; cursor fields left None (not used for stop logic)
        save_ingest_state(
            conn,
            RedditIngestState(
                subreddit=options.subreddit,
                sort_mode="top",
                last_post_created_utc=None,
                last_post_id=None,
                last_scrape_time=now_str,
            ),
        )

        conn.commit()
        return IngestResult(
            sort="top",
            subreddit=options.subreddit,
            pages_fetched=fetch_result.pages_fetched,
            posts_stored=posts_stored,
            posts_skipped=posts_skipped,
            targets_stored=targets_stored,
            targets_deduped=targets_deduped,
            parse_errors=parse_errors,
            stopped_by_cursor=False,
            stopped_by_max_posts=stopped_by_max_posts,
            replace_cache_done=replace_cache_done,
            rate_limited=False,
            error=None,
        )
    except sqlite3.Error as e:
        conn.rollback()
        return _error_result(
            options,
            replace_cache_done,
            pages_fetched=fetch_result.pages_fetched,
            error=str(e),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ingest(options: IngestOptions, db_path: Optional[Path] = None) -> IngestResult:
    """
    Run one ingestion cycle for the given options.

    Always returns an IngestResult — never raises. On any failure the result
    carries error/rate_limited fields; counts reflect work done up to failure.
    """
    # --- Validation ---
    if options.sort not in {"new", "top"}:
        return _error_result(options, False, error=f"invalid sort: {options.sort!r}")
    if not (1 <= options.max_posts <= 200):
        return _error_result(options, False, error=f"invalid max_posts: {options.max_posts}")
    if not (1 <= options.max_pages <= 3):
        return _error_result(options, False, error=f"invalid max_pages: {options.max_pages}")
    if options.subreddit != "opendirectories":
        return _error_result(
            options,
            False,
            error=(
                f"unsupported subreddit: {options.subreddit!r}"
                " (only 'opendirectories' supported)"
            ),
        )

    # --- Setup (wipe + schema init) ---
    # wipe_all commits its own transaction before init_db or fetch run.
    # If fetch later fails, the DB remains wiped — replace_cache_done=True signals this.
    replace_cache_done = False
    try:
        if options.replace_cache:
            wipe_all(db_path)
            replace_cache_done = True
        init_db(db_path)
    except (sqlite3.Error, OSError) as e:
        return _error_result(options, replace_cache_done, error=str(e))

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # --- Fetch (no write connection open yet) ---
    try:
        fetch_result = fetch_posts(options.sort, options.max_pages)
    except RateLimitError:
        return _error_result(options, replace_cache_done, rate_limited=True, error="HTTP 429")
    except FetchError as e:
        return _error_result(options, replace_cache_done, error=str(e))

    # --- Dispatch ---
    if options.sort == "new":
        return _run_new(options, fetch_result, db_path, now_str, replace_cache_done)
    return _run_top(options, fetch_result, db_path, now_str, replace_cache_done)
