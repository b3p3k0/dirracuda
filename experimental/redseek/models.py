from dataclasses import dataclass
from typing import Optional


DEFAULT_MAX_POSTS = 100
MAX_POSTS = 100


@dataclass
class RedditPost:
    post_id: str
    post_title: str
    post_author: Optional[str]
    post_created_utc: float
    is_nsfw: int          # 0 or 1
    had_targets: int      # 0 or 1
    source_sort: str      # "new", "top", "search"; "user" may exist in historical rows
    last_seen_at: str     # UTC datetime string: "YYYY-MM-DD HH:MM:SS"


@dataclass
class RedditTarget:
    id: Optional[int]               # None before insert (AUTOINCREMENT)
    post_id: str
    target_raw: str
    target_normalized: str
    host: Optional[str]
    protocol: Optional[str]         # "http", "https", "ftp", "unknown"
    notes: Optional[str]
    parse_confidence: Optional[str]  # "high", "medium", "low"
    created_at: str                 # UTC datetime string
    dedupe_key: str                 # sha1(post_id + "|" + target_normalized)
    probe_status: str = "unprobed"
    probe_indicator_matches: int = 0
    probe_preview: Optional[str] = None
    probe_checked_at: Optional[str] = None
    probe_error: Optional[str] = None
    probe_snapshot_json: Optional[str] = None


@dataclass
class RedditIngestState:
    subreddit: str
    sort_mode: str                       # "new", "top:<window>", "search:<sort>:<window_or_na>:<query>"; historical rows may use "user:*"
    last_post_created_utc: Optional[float]
    last_post_id: Optional[str]
    last_scrape_time: Optional[str]      # UTC datetime string
