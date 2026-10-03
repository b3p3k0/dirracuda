"""Provider rediscovery preserves server history without copying it to new rows."""

import socket
import sqlite3

import pytest

from commands.ftp.models import FtpAccessOutcome
from commands.http.models import HttpAccessOutcome
from experimental.redseek import store as reddit_store
from experimental.redseek.main_db_sync import sync_targets_to_main_db
from experimental.redseek.models import RedditPost, RedditTarget
from experimental.se_dork import store as search_store
from experimental.se_dork.main_db_sync import sync_run_to_main_db
from experimental.se_dork.models import RunOptions
from gui.utils.database_access import DatabaseReader
from shared.database_ftp_persistence import FtpPersistence
from shared.database_http_persistence import HttpPersistence
from tools.db_manager import DatabaseManager, SMBSeekDataAccessLayer


IP = "192.0.2.10"
NOTE = "Posted to r/OpenDirectories by u/alice on 2023-11-14 (UTC) original title: Archive"


@pytest.fixture
def db(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("This test must not contact a live host")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    path = tmp_path / "providers.db"
    manager = DatabaseManager(str(path))
    manager.close()
    return path


def _reddit_host(db, protocol="http"):
    reddit_store.init_db(db)
    url = f"{protocol}://{IP}/original/"
    with reddit_store.open_connection(db) as conn:
        reddit_store.upsert_post(conn, RedditPost(
            "p1", "Archive", "alice", 1700000000, 0, 1, "new", "2026-10-02",
        ))
        reddit_store.upsert_targets(conn, [RedditTarget(
            None, "p1", url, url, IP, protocol, None, "high", "2026-10-02", "k1",
        )])
    assert sync_targets_to_main_db(["k1"], db_path=db)["inserted"] == 1


def _rows(db):
    rows, total = DatabaseReader(str(db), cache_duration=0).get_protocol_server_list(limit=None)
    assert len(rows) == total
    return {(r["host_type"], r["ip_address"], r["port"]): r for r in rows}


def _shodan_write(db, protocol, ip=IP, port=80):
    if protocol == "http":
        return HttpPersistence(str(db)).upsert_http_server(
            ip, "United States", "US", port, "http", "HTTP server", "Index of /", "{}",
        )
    if protocol == "ftp":
        return FtpPersistence(str(db)).upsert_ftp_server(
            ip, "United States", "US", 21, True, "FTP server", "{}",
        )
    manager = DatabaseManager(str(db))
    try:
        return SMBSeekDataAccessLayer(manager).get_or_create_server(
            ip, country="United States", country_code="US", auth_method="Anonymous",
        )
    finally:
        manager.close()


def _search_run(db, urls):
    search_store.init_db(db)
    with search_store.open_connection(db) as conn:
        run_id = search_store.insert_run(conn, RunOptions(
            instance_url="http://search.invalid", query="archive", max_results=10,
        ), "2026-10-02T12:00:00")
        for url in urls:
            search_store.insert_result(conn, run_id, {
                "url": url, "title": "Index of /", "content": "Parent directory",
                "engine": "test", "engines": ["test"],
            })
    return sync_run_to_main_db(run_id, db_path=db)


@pytest.mark.parametrize("protocol,key", [
    ("http", ("H", IP, 80)), ("ftp", ("F", IP, 21)), ("smb", ("S", IP, None)),
])
def test_shodan_rediscovery_preserves_reddit_notes_and_record_id(db, protocol, key):
    _reddit_host(db, protocol)
    before = _rows(db)[key]
    reader = DatabaseReader(str(db))
    reader.upsert_user_flags_for_host(IP, key[0], notes=NOTE + "\nMy notes", favorite=True)
    assert _shodan_write(db, protocol) == before["protocol_server_id"]
    assert _shodan_write(db, protocol) == before["protocol_server_id"]
    after = _rows(db)[key]
    assert after["notes"] == NOTE + "\nMy notes"
    assert after["favorite"] == 1
    assert after["scan_count"] == before["scan_count"] + 2
    _shodan_write(db, protocol, ip="192.0.2.11")
    assert _rows(db)[(key[0], "192.0.2.11", key[2])]["notes"] == ""


def test_search_rediscovery_preserves_notes_but_new_ip_or_port_does_not_inherit(db):
    _reddit_host(db)
    before = _rows(db)[("H", IP, 80)]
    urls = [f"http://{IP}/another/path/", "http://192.0.2.11/", f"http://{IP}:8080/"]
    for expected in [(2, 1), (0, 3)]:
        summary = _search_run(db, urls)
        assert summary["failed"] == 0
        assert (summary["inserted"], summary["updated"]) == expected
        rows = _rows(db)
        assert rows[("H", IP, 80)]["protocol_server_id"] == before["protocol_server_id"]
        assert rows[("H", IP, 80)]["notes"] == NOTE
        assert rows[("H", "192.0.2.11", 80)]["notes"] == ""
        assert rows[("H", IP, 8080)]["notes"] == ""


def test_same_numeric_id_in_other_protocol_tables_does_not_leak_notes(db):
    _reddit_host(db)
    _shodan_write(db, "smb")
    _shodan_write(db, "ftp")
    _shodan_write(db, "http", port=8080)
    rows = _rows(db)
    assert rows[("H", IP, 80)]["protocol_server_id"] == rows[("S", IP, None)]["protocol_server_id"]
    assert rows[("H", IP, 80)]["protocol_server_id"] == rows[("F", IP, 21)]["protocol_server_id"]
    assert rows[("H", IP, 80)]["notes"] == NOTE
    assert rows[("S", IP, None)]["notes"] == ""
    assert rows[("F", IP, 21)]["notes"] == ""
    assert rows[("H", IP, 8080)]["notes"] == ""


def test_http_hostname_and_path_are_not_separate_record_identities(db, monkeypatch):
    _reddit_host(db)
    before = _rows(db)[("H", IP, 80)]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (IP, 80)),
    ])
    summary = _search_run(db, ["http://different-host.invalid/other-directory/"])
    assert summary["failed"] == 0
    assert summary["updated"] == 1
    assert summary["inserted"] == 0
    after = _rows(db)[("H", IP, 80)]
    assert after["protocol_server_id"] == before["protocol_server_id"]
    assert after["notes"] == NOTE
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT probe_host, probe_path FROM http_servers").fetchone() == (
            "different-host.invalid", "/other-directory/",
        )


@pytest.mark.parametrize("protocol", ["http", "ftp"])
def test_verified_shodan_batch_keeps_notes_while_refreshing_access_results(db, protocol):
    _reddit_host(db, protocol)
    if protocol == "http":
        outcome = HttpAccessOutcome(
            IP, "United States", "US", 80, "http", "", "Index of /", "{}",
            True, 200, True, 1, 2, False, "", "", "{}",
        )
        persistence = HttpPersistence(str(db))
        key = ("H", IP, 80)
    else:
        outcome = FtpAccessOutcome(
            IP, "United States", "US", 21, "", "{}", True,
            "anonymous", True, 3, "", "{}",
        )
        persistence = FtpPersistence(str(db))
        key = ("F", IP, 21)
    before = _rows(db)[key]
    persistence.persist_access_outcomes_batch([outcome])
    after = _rows(db)[key]
    assert after["protocol_server_id"] == before["protocol_server_id"]
    assert after["notes"] == NOTE
    assert after["accessible_shares"] == 3
