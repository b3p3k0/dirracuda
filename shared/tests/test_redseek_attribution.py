"""Reddit attribution must reach editable server notes without losing user data."""

import sqlite3
from unittest.mock import MagicMock

import pytest

from experimental.redseek.main_db_sync import sync_targets_to_main_db
from experimental.redseek.mapper import row_to_prefill
from experimental.redseek.models import RedditPost, RedditTarget
from experimental.redseek.service import IngestOptions, run_ingest
from experimental.redseek.store import init_db, open_connection, upsert_post, upsert_targets
from gui.utils.database_access import DatabaseReader
from gui.utils.sidecar_promotion import promote_sidecar_prefill


TITLE = "An archive of music & books — " + "full title " * 20 + "THE END"
NOTE = f"Posted to r/OpenDirectories by u/alice on 2023-11-14 (UTC) original title: {TITLE}"
TABLES = {"smb": "host_user_flags", "ftp": "ftp_user_flags", "http": "http_user_flags"}


def _seed(db, protocol="http", post_id="p1", title=TITLE, author="alice", port=None):
    init_db(db)
    endpoint = "192.0.2.1" + (f":{port}" if port else "")
    url = f"{protocol}://{endpoint}/files/"
    key = f"{post_id}:{url}"
    with open_connection(db) as conn:
        upsert_post(conn, RedditPost(post_id, title, author, 1700000000, 0, 1, "new", "2026-09-27"))
        upsert_targets(conn, [RedditTarget(
            None, post_id, url, url, "192.0.2.1", protocol,
            "T:truncated preview", "high", "2026-09-27", key,
        )])
    return key


def _flags(db, protocol="http"):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            f"SELECT notes, favorite, avoid FROM {TABLES[protocol]} ORDER BY server_id"
        ).fetchall()


@pytest.mark.parametrize("protocol", ["http", "ftp", "smb"])
def test_sync_stores_full_attribution_once_and_preserves_user_notes(tmp_path, protocol):
    db = tmp_path / "main.db"
    key = _seed(db, protocol)
    first = sync_targets_to_main_db([key], db_path=db)
    assert first["inserted"] == 1
    assert first["failed"] == 0
    assert _flags(db, protocol) == [(NOTE, 0, 0)]

    reader = DatabaseReader(str(db))
    user_notes = "My notes: keep this host.\n" + NOTE
    reader.upsert_user_flags_for_host(
        "192.0.2.1", {"http": "H", "ftp": "F", "smb": "S"}[protocol],
        favorite=True, avoid=True, notes=user_notes,
    )
    second = sync_targets_to_main_db([key], db_path=db)
    assert second["updated"] == 1
    assert second["failed"] == 0
    assert _flags(db, protocol) == [(user_notes, 1, 1)]

    another = _seed(db, protocol, post_id="p2", title="Another post", author="bob")
    assert sync_targets_to_main_db([another], db_path=db)["failed"] == 0
    assert _flags(db, protocol) == [(
        user_notes + "\nPosted to r/OpenDirectories by u/bob on 2023-11-14 (UTC) original title: Another post", 1, 1,
    )]


def test_http_attribution_stays_on_matching_port(tmp_path):
    db = tmp_path / "main.db"
    first = _seed(db, port=80)
    second = _seed(db, post_id="p2", title="Other endpoint", port=8080)
    assert sync_targets_to_main_db([first, second], db_path=db)["inserted"] == 2
    assert [row[0] for row in _flags(db)] == [
        NOTE, "Posted to r/OpenDirectories by u/alice on 2023-11-14 (UTC) original title: Other endpoint",
    ]


@pytest.mark.parametrize("mode,sort", [("feed", "new"), ("feed", "top"), ("search", "new")])
def test_mocked_atom_ingest_through_sync_to_notes(tmp_path, monkeypatch, mode, sort):
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>t3_p1</id><title>Books &amp; music: the complete title</title>
      <author><name>/u/alice</name></author>
      <published>2026-09-27T12:00:00+00:00</published>
      <content type="html">http://192.0.2.1/files/</content></entry>
    </feed>'''
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: response)
    db = tmp_path / "main.db"
    result = run_ingest(IngestOptions(
        mode=mode, sort=sort, query="books", max_posts=100,
        parse_body=True, include_nsfw=False, replace_cache=False,
    ), db)
    assert result.error is None
    assert result.targets_stored == 1
    synced = sync_targets_to_main_db(result._probe_candidate_keys, db_path=db)
    assert synced["inserted"] == 1
    assert synced["failed"] == 0
    assert _flags(db) == [(
        "Posted to r/OpenDirectories by u/alice on 2026-09-27 (UTC) original title: Books & music: the complete title", 0, 0,
    )]


@pytest.mark.parametrize("author,expected", [
    (None, "[unknown]"), ("", "[unknown]"), (123, "[unknown]"),
    ("[deleted]", "[deleted]"), ("/u/alice", "alice"), ("u/alice", "alice"),
])
def test_legacy_promotion_handles_author_metadata(tmp_path, author, expected):
    db = tmp_path / "main.db"
    reader = DatabaseReader(str(db))
    prefill = row_to_prefill({
        "host": "192.0.2.1", "protocol": "http",
        "target_normalized": "http://192.0.2.1/", "post_title": TITLE, "post_author": author,
    }, promotion_source="reddit_browser", snapshot_source="sidecar:reddit")
    promote_sidecar_prefill(reader, prefill)
    assert _flags(db) == [(
        f"Posted to r/OpenDirectories by u/{expected} original title: {TITLE}", 0, 0,
    )]


@pytest.mark.parametrize("title", [None, "", "   ", 123])
def test_missing_or_invalid_title_does_not_promote_preview_as_title(title):
    prefill = row_to_prefill({
        "host": "192.0.2.1", "protocol": "http", "post_title": title,
        "notes": "T:a shortened title", "post_author": "alice",
    }, promotion_source="reddit_browser", snapshot_source="sidecar:reddit")
    assert prefill is not None
    assert "_append_notes" not in prefill


@pytest.mark.parametrize("shape", ["missing", "no_notes", "minimal"])
def test_notes_write_checks_actual_legacy_schema(tmp_path, shape):
    db = tmp_path / "main.db"
    reader = DatabaseReader(str(db))
    # Change schema after construction so startup migrations cannot hide the case.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE http_user_flags")
        if shape == "no_notes":
            conn.execute("CREATE TABLE http_user_flags (server_id INTEGER PRIMARY KEY)")
        elif shape == "minimal":
            conn.execute("CREATE TABLE http_user_flags (server_id INTEGER PRIMARY KEY, notes TEXT)")
    payload = {"host_type": "H", "ip_address": "192.0.2.1", "_append_notes": NOTE}
    assert reader.upsert_manual_server_record(payload)["operation"] == "insert"
    assert reader.upsert_manual_server_record(payload)["operation"] == "update"
    if shape == "minimal":
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT notes FROM http_user_flags").fetchone() == (NOTE,)


def test_note_failure_rolls_back_server_insert(tmp_path):
    db = tmp_path / "main.db"
    reader = DatabaseReader(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TRIGGER reject_note BEFORE INSERT ON http_user_flags
                        BEGIN SELECT RAISE(ABORT, 'test note failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="test note failure"):
        reader.upsert_manual_server_record({
            "host_type": "H", "ip_address": "192.0.2.1", "_append_notes": NOTE,
        })
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM http_servers").fetchone()[0] == 0


def test_distinct_titles_that_share_prefix_are_both_kept(tmp_path):
    db = tmp_path / "main.db"
    longer = _seed(db, title="Archive extended")
    shorter = _seed(db, post_id="p2", title="Archive")
    assert sync_targets_to_main_db([longer, shorter], db_path=db)["failed"] == 0
    notes = _flags(db)[0][0]
    assert notes.splitlines() == [
        "Posted to r/OpenDirectories by u/alice on 2023-11-14 (UTC) original title: Archive extended",
        "Posted to r/OpenDirectories by u/alice on 2023-11-14 (UTC) original title: Archive",
    ]


@pytest.mark.parametrize("timestamp,date_text", [
    (1700000000, " on 2023-11-14 (UTC)"),
    (1700000000.5, " on 2023-11-14 (UTC)"),
    ("1700000000", " on 2023-11-14 (UTC)"),
    (1700006400, " on 2023-11-15 (UTC)"),  # UTC midnight, still Nov 14 in New York
    (0, " on 1970-01-01 (UTC)"),
    (None, ""), ("", ""), ("bad date", ""), (True, ""), (False, ""),
    (float("nan"), ""), (float("inf"), ""), (1e100, ""), ([], ""),
])
def test_post_date_format_and_invalid_timestamp_fallback(timestamp, date_text):
    prefill = row_to_prefill({
        "host": "192.0.2.1", "protocol": "http", "post_title": TITLE,
        "post_author": "alice", "post_created_utc": timestamp,
    }, promotion_source="reddit_browser", snapshot_source="sidecar:reddit")
    assert prefill is not None
    assert prefill["_append_notes"] == (
        f"Posted to r/OpenDirectories by u/alice{date_text} original title: {TITLE}"
    )


def test_dated_attribution_preserves_existing_undated_notes(tmp_path):
    db = tmp_path / "main.db"
    key = _seed(db)
    reader = DatabaseReader(str(db))
    reader.upsert_manual_server_record({"host_type": "H", "ip_address": "192.0.2.1"})
    existing = f"My own note\nPosted to r/OpenDirectories by u/alice original title: {TITLE}"
    reader.upsert_user_flags_for_host("192.0.2.1", "H", notes=existing)
    for _ in range(2):
        assert sync_targets_to_main_db([key], db_path=db)["failed"] == 0
    assert _flags(db)[0][0] == existing + "\n" + NOTE
