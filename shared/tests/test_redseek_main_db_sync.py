"""Unit tests for experimental/redseek/main_db_sync.py."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from experimental.redseek.main_db_sync import sync_targets_to_main_db
from experimental.redseek.models import RedditPost, RedditTarget
from experimental.redseek.store import (
    init_db,
    open_connection,
    upsert_post,
    upsert_targets,
)

_NOW = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_post(post_id: str = "p1") -> RedditPost:
    return RedditPost(
        post_id=post_id,
        post_title="Test Post",
        post_author="alice",
        post_created_utc=1_700_000_000.0,
        is_nsfw=0,
        had_targets=1,
        source_sort="new",
        last_seen_at=_NOW,
    )


def _make_target(
    post_id: str = "p1",
    dedupe_key: str = "key1",
    protocol: str = "http",
    host: str = "1.2.3.4",
    target_normalized: str = "http://1.2.3.4/files/",
) -> RedditTarget:
    return RedditTarget(
        id=None,
        post_id=post_id,
        target_raw=target_normalized,
        target_normalized=target_normalized,
        host=host,
        protocol=protocol,
        notes=None,
        parse_confidence="high",
        created_at=_NOW,
        dedupe_key=dedupe_key,
    )


def _seed(db_path: Path, targets: list[RedditTarget], post_id: str = "p1") -> None:
    init_db(db_path)
    with open_connection(db_path) as conn:
        upsert_post(conn, _make_post(post_id))
        upsert_targets(conn, targets)
        conn.commit()


# ---------------------------------------------------------------------------
# Row-loading and skip accounting
# ---------------------------------------------------------------------------

def test_sync_counts_protocol_skips(tmp_path: Path) -> None:
    db = tmp_path / "main.db"
    targets = [
        _make_target("p1", "k1", protocol="http", host="1.2.3.4"),
        _make_target("p1", "k2", protocol="gopher", host="1.2.3.5"),  # unsupported
        _make_target("p1", "k3", protocol="", host="1.2.3.6"),        # empty protocol
    ]
    _seed(db, targets)

    summary = sync_targets_to_main_db(["k1", "k2", "k3"], db_path=db)

    assert summary["selected"] == 3
    assert summary["inserted"] == 1
    assert summary["skipped"] >= 2  # k2 and k3 are shape-skipped


def test_sync_counts_hostless_skips(tmp_path: Path) -> None:
    db = tmp_path / "main.db"
    targets = [
        _make_target("p1", "k1", protocol="http", host=""),   # empty host
        _make_target("p1", "k2", protocol="ftp", host="1.2.3.4"),
    ]
    _seed(db, targets)

    summary = sync_targets_to_main_db(["k1", "k2"], db_path=db)

    assert summary["selected"] == 2
    assert summary["skipped"] >= 1   # k1 shape-skipped


def test_sync_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "main.db"
    _seed(db, [_make_target("p1", "k1", protocol="http", host="1.2.3.4")])

    first = sync_targets_to_main_db(["k1"], db_path=db)
    second = sync_targets_to_main_db(["k1"], db_path=db)

    assert first["inserted"] == 1
    assert first["failed"] == 0
    assert second["updated"] == 1
    assert second["inserted"] == 0
    assert second["failed"] == 0


def test_sync_empty_keys(tmp_path: Path) -> None:
    db = tmp_path / "main.db"
    summary = sync_targets_to_main_db([], db_path=db)
    assert summary["selected"] == 0
    assert summary["inserted"] == 0
    assert summary["failed"] == 0
    assert "error" not in summary


# ---------------------------------------------------------------------------
# Never-raises guarantees
# ---------------------------------------------------------------------------

def test_sync_never_raises_on_store_error(tmp_path: Path, monkeypatch) -> None:
    import experimental.redseek.main_db_sync as _mod

    def _boom(*a, **kw):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(_mod.reddit_store, "get_targets_by_dedupe_keys", _boom)

    db = tmp_path / "main.db"
    init_db(db)
    summary = sync_targets_to_main_db(["k1"], db_path=db)

    assert "error" in summary
    assert "store exploded" in summary["error"]


def test_sync_never_raises_on_promotion_exception(tmp_path: Path, monkeypatch) -> None:
    import experimental.redseek.main_db_sync as _mod

    _seed(tmp_path / "main.db", [_make_target("p1", "k1", protocol="http", host="1.2.3.4")])
    db = tmp_path / "main.db"

    def _boom(*a, **kw):
        raise RuntimeError("promotion layer exploded")

    monkeypatch.setattr(_mod, "promote_sidecar_prefills", _boom)

    summary = sync_targets_to_main_db(["k1"], db_path=db)

    assert "error" in summary
    assert "promotion layer exploded" in summary["error"]
    assert summary["failed"] >= 0   # deterministic shape; no propagation
