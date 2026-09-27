"""Shipping and upgrade contracts for the curated Dorkbook collection."""

from contextlib import closing

from experimental.dorkbook import store
from experimental.dorkbook.catalog import DIRECTORY_IDEAS
from experimental.dorkbook.models import DEFAULT_BUILTIN_DORKS


ORIGINAL_QUERIES = {
    "builtin_smb_default": "smb authentication: disabled",
    "builtin_ftp_default": 'port:21 "230 Login successful"',
    "builtin_http_default": 'http.title:"Index of /"',
}
SELECTED_IDS = set(range(1, 11)) | {12, 13, 14, 19, 20, 21, 22, 26, 27, 28, 29, 32}


def test_catalog_preserves_original_queries_and_covers_selected_ideas():
    by_key = {dork.builtin_key: dork for dork in DEFAULT_BUILTIN_DORKS}
    assert len(by_key) == len(DEFAULT_BUILTIN_DORKS) == 52
    assert {key: by_key[key].query for key in ORIGINAL_QUERIES} == ORIGINAL_QUERIES
    assert {idea.candidate_id for idea in DIRECTORY_IDEAS} == SELECTED_IDS
    assert len(DIRECTORY_IDEAS) == len(SELECTED_IDS)
    for idea in DIRECTORY_IDEAS:
        for provider in ("shodan", "self_hosted"):
            dork = by_key[f"builtin_{provider}_{idea.key}"]
            assert dork.provider == provider
            assert dork.protocol == ("HTTP" if provider == "shodan" else None)
            assert dork.topic == idea.topic
            assert idea.clue in dork.query


def test_catalog_has_unique_destinations_and_readable_content():
    identities = {(d.provider, d.protocol, d.query.strip()) for d in DEFAULT_BUILTIN_DORKS}
    assert len(identities) == len(DEFAULT_BUILTIN_DORKS)
    assert len({d.topic for d in DEFAULT_BUILTIN_DORKS}) == 7
    for dork in DEFAULT_BUILTIN_DORKS:
        assert dork.nickname.strip() and dork.notes.strip() and dork.topic.strip()
        assert "recipe" not in dork.nickname.lower()
        assert dork.query == dork.query.strip()
        assert "\n" not in dork.query
        assert len(dork.query) < 100
        if dork.provider == "self_hosted":
            assert dork.protocol is None
            assert dork.query.startswith('intitle:"')
            assert "http.html:" not in dork.query
            assert "filetype:" not in dork.query  # Find listings, not the files themselves.


def test_catalog_includes_both_approved_title_variants_and_web_default():
    by_key = {dork.builtin_key: dork for dork in DEFAULT_BUILTIN_DORKS}
    assert by_key["builtin_self_hosted_default"].query == 'intitle:"Index of /"'
    for provider, title_filter in (("shodan", "http.title"), ("self_hosted", "intitle")):
        assert by_key[f"builtin_{provider}_directory_listing"].query == f'{title_filter}:"Directory listing"'
        assert by_key[f"builtin_{provider}_index_of"].query == f'{title_filter}:"Index of"'


def test_reseeding_full_catalog_preserves_ids_and_timestamps(tmp_path, monkeypatch):
    path = tmp_path / "dorkbook.db"
    monkeypatch.setattr(store, "_utcnow", lambda: "2026-09-27T01:00:00")
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        before = store.list_entries(conn, provider=None)
    assert len(before) == 52
    monkeypatch.setattr(store, "_utcnow", lambda: "2026-09-27T02:00:00")
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        assert store.list_entries(conn, provider=None) == before


def test_catalog_upgrade_keeps_custom_collision_and_existing_default(tmp_path, monkeypatch):
    path = tmp_path / "dorkbook.db"
    original = tuple(d for d in DEFAULT_BUILTIN_DORKS if d.builtin_key in ORIGINAL_QUERIES)
    monkeypatch.setattr(store, "DEFAULT_BUILTIN_DORKS", original)
    store.init_db(path)
    additions = [d for d in DEFAULT_BUILTIN_DORKS if d.builtin_key.endswith("_epub")]
    with closing(store.open_connection(path)) as conn:
        originals_before = store.list_entries(conn, provider=None)
        custom_ids = [store.create_entry(
            conn, dork.protocol, "My EPUB dork", dork.query, "Keep my notes",
            provider=dork.provider, topic="My books",
        ) for dork in additions]
        conn.commit()
    monkeypatch.setattr(store, "DEFAULT_BUILTIN_DORKS", DEFAULT_BUILTIN_DORKS)
    store.init_db(path)
    with closing(store.open_connection(path)) as conn:
        for original_entry in originals_before:
            assert store.get_entry(conn, original_entry["entry_id"]) == original_entry
        for entry_id in custom_ids:
            custom = store.get_entry(conn, entry_id)
            assert custom["row_kind"] == "custom"
            assert custom["nickname"] == "My EPUB dork"
            assert custom["notes"] == "Keep my notes"
            assert custom["topic"] == "My books"
        rows = store.list_entries(conn, provider=None)
        assert len(rows) == 52
        assert not {d.builtin_key for d in additions} & {row["builtin_key"] for row in rows}
