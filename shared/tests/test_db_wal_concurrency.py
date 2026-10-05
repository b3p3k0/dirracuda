"""CPM-B1: primary DB pragmas, thread lifetime, and GUI/CLI concurrency."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
import gc
import logging
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import warnings
import weakref

import pytest

from gui.utils.database_access import DatabaseReader
from shared.db_connection import configure_primary_connection
from shared.db_migrations import run_migrations
from tools.db_manager import DatabaseManager


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def make_reader(tmp_path, monkeypatch):
    # Migrations must not import the developer's saved favorites into test DBs.
    monkeypatch.setattr("shared.db_migrations._import_legacy_settings", lambda cur: None)
    readers = []

    def make(name="primary.db", *, bare=False):
        path = tmp_path / name
        if bare:
            reader = object.__new__(DatabaseReader)
            reader.db_path = path
        else:
            reader = DatabaseReader(str(path))
        readers.append(reader)
        return reader

    yield make
    for reader in readers:
        reader.close_thread_connection()


@pytest.fixture
def reader(make_reader):
    reader = make_reader()
    with reader._get_connection() as conn:
        conn.execute("CREATE TABLE cpm_rows (value INTEGER PRIMARY KEY)")
        conn.commit()
    return reader


def _count(reader):
    with reader._get_connection() as conn:
        return conn.execute("SELECT count(*) FROM cpm_rows").fetchone()[0]


def _insert(reader, value):
    with reader._get_connection() as conn:
        conn.execute("INSERT INTO cpm_rows VALUES (?)", (value,))
        conn.commit()


def _subprocess_kwargs():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    return dict(cwd=REPO_ROOT, env=env, text=True)


def _cli_code(path, body):
    return (
        "from tools.db_manager import DatabaseManager\n"
        "import sys, time\n"
        f"manager = DatabaseManager({str(path)!r})\n"
        "conn = manager.get_connection()\n"
        + textwrap.dedent(body)
        + "\nmanager.close()\n"
    )


@contextmanager
def _cli_process(code):
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **_subprocess_kwargs(),
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=60)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def _read_line(process, timeout=10):
    # Read bytes only after readiness, with a deadline even for partial lines.
    deadline = time.monotonic() + timeout
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while not data.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            assert remaining > 0 and selector.select(timeout=remaining), "CLI output timed out"
            chunk = os.read(process.stdout.fileno(), 1)
            assert chunk, "CLI exited before its expected output"
            data.extend(chunk)
    return data.decode().strip()


def _finish_cli(process):
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, (stdout, stderr)
    assert "database is locked" not in stderr.lower()
    return stdout


def test_t1_reader_pragmas_and_sticky_header(tmp_path, make_reader):
    reader = make_reader(bare=True)
    with reader._get_connection() as conn:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        conn.execute("CREATE TABLE sticky (value INTEGER)")
    reader.close_thread_connection()
    assert (tmp_path / "primary.db").read_bytes()[18:20] == b"\x02\x02"


def test_t2_wal_sticks_across_openers(reader):
    with reader._get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    reader.close_thread_connection()
    manager = DatabaseManager(str(reader.db_path))
    try:
        conn = manager.get_connection()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        manager.close()
    with closing(sqlite3.connect(reader.db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_t3_migrations_configure_fresh_database(tmp_path, monkeypatch):
    path = tmp_path / "migrations.db"
    real_configure = configure_primary_connection
    configured = []

    def configure(conn):
        result = real_configure(conn)
        configured.append(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        return result

    monkeypatch.setattr("shared.db_connection.configure_primary_connection", configure)
    monkeypatch.setattr("shared.db_migrations._import_legacy_settings", lambda cur: None)
    run_migrations(str(path))
    assert configured == [30000]
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_t4_thread_local_identity_and_close(reader):
    with reader._get_connection() as first:
        pass
    with reader._get_connection() as second:
        assert second is first

    def worker():
        with reader._get_connection() as other:
            assert other is not first
        reader.close_thread_connection()
        with pytest.raises(sqlite3.ProgrammingError):
            other.execute("SELECT 1")
        with reader._get_connection() as reopened:
            assert reopened is not other
        reader.close_thread_connection()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result(timeout=10)
    assert first.execute("SELECT 1").fetchone()[0] == 1
    reader.close_thread_connection()
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")


def test_t5_checkouts_do_not_serialize_threads(reader):
    barrier = threading.Barrier(2, timeout=5)

    def worker():
        try:
            with reader._get_connection():
                barrier.wait(timeout=5)
        finally:
            reader.close_thread_connection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)


def test_t6_read_during_uncommitted_write(reader, make_reader):
    observer = make_reader()
    ready = threading.Event()
    release = threading.Event()

    def writer():
        try:
            with reader._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO cpm_rows VALUES (1)")
                ready.set()
                assert release.wait(timeout=10)
                conn.commit()
        finally:
            reader.close_thread_connection()

    def read():
        try:
            start = time.monotonic()
            with observer._get_connection() as conn:
                count = conn.execute("SELECT count(*) FROM cpm_rows").fetchone()[0]
                assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            return count, time.monotonic() - start
        finally:
            observer.close_thread_connection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer)
        try:
            assert ready.wait(timeout=5)
            count, elapsed = pool.submit(read).result(timeout=1)
            assert count == 0
            assert elapsed < 1
        finally:
            release.set()
        writing.result(timeout=10)
    assert _count(observer) == 1


def test_t7_twelve_probe_cache_writers(reader):
    addresses = [f"192.0.{worker}.{item + 1}" for worker in range(12) for item in range(25)]
    with reader._get_connection() as conn:
        conn.executemany("INSERT INTO smb_servers (ip_address) VALUES (?)", [(ip,) for ip in addresses])
        conn.commit()
    barrier = threading.Barrier(12, timeout=10)

    def worker(index):
        try:
            barrier.wait(timeout=10)
            for ip in addresses[index * 25:(index + 1) * 25]:
                # The SMB probe persistence shim routes to this production upsert.
                reader.upsert_probe_cache_for_host(
                    ip, "S", status="accessible", indicator_matches=1,
                    snapshot_path=f"snapshots/{ip}.json",
                )
        finally:
            reader.close_thread_connection()

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(worker, index) for index in range(12)]
        for future in futures:
            future.result(timeout=60)
    with reader._get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM host_probe_cache").fetchone()[0] == 300
        assert conn.execute(
            "SELECT count(*) FROM host_probe_cache WHERE status='accessible' AND indicator_matches=1"
        ).fetchone()[0] == 300


def test_t8a_cli_and_gui_concurrent_writes(reader):
    code = _cli_code(reader.db_path, """
        import select
        print('READY', flush=True)
        assert select.select([sys.stdin], [], [], 10)[0]
        assert sys.stdin.readline().strip() == 'GO'
        for value in range(200):
            conn.execute('INSERT INTO cpm_rows VALUES (?)', (value,))
            conn.commit()
            time.sleep(0.001)
    """)
    barrier = threading.Barrier(5, timeout=10)

    def read():
        try:
            barrier.wait(timeout=10)
            counts = []
            for _ in range(200):
                counts.append(_count(reader))
                time.sleep(0.001)
            assert counts == sorted(counts)
        finally:
            reader.close_thread_connection()

    def write(offset):
        try:
            barrier.wait(timeout=10)
            for value in range(offset, offset + 100):
                _insert(reader, value)
                time.sleep(0.001)
        finally:
            reader.close_thread_connection()

    with _cli_process(code) as process, ThreadPoolExecutor(max_workers=4) as pool:
        assert _read_line(process) == "READY"
        futures = [pool.submit(read), pool.submit(read), pool.submit(write, 200), pool.submit(write, 300)]
        process.stdin.write("GO\n")
        process.stdin.flush()
        barrier.wait(timeout=10)
        for future in futures:
            future.result(timeout=60)
        _finish_cli(process)
    assert _count(reader) == 400


def test_t8b_cli_holds_write_lock(reader):
    code = _cli_code(reader.db_path, """
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT INTO cpm_rows VALUES (1)')
        print('READY', flush=True)
        time.sleep(2)
        conn.commit()
    """)
    with _cli_process(code) as process:
        assert _read_line(process) == "READY"
        start = time.monotonic()
        assert _count(reader) == 0
        assert time.monotonic() - start < 0.5
        start = time.monotonic()
        _insert(reader, 2)
        elapsed = time.monotonic() - start
        assert 1 < elapsed < 30
        _finish_cli(process)
    assert _count(reader) == 2


def test_t8c_gui_holds_write_lock(reader):
    code = _cli_code(reader.db_path, """
        start = time.monotonic()
        assert conn.execute('SELECT count(*) FROM cpm_rows').fetchone()[0] == 0
        print('READ', flush=True)
        assert time.monotonic() - start < 0.5
        start = time.monotonic()
        conn.execute('INSERT INTO cpm_rows VALUES (2)')
        conn.commit()
        print(time.monotonic() - start, flush=True)
    """)
    with reader._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO cpm_rows VALUES (1)")
        with _cli_process(code) as process:
            try:
                start = time.monotonic()
                assert _read_line(process, timeout=5) == "READ"
                assert time.monotonic() - start < 1
                # Hold for two more seconds after the CLI proves its read finished.
                time.sleep(2)
            finally:
                conn.commit()
            elapsed = float(_finish_cli(process).strip())
            assert 1 < elapsed < 30
    assert _count(reader) == 2


def test_t9a_exception_rolls_back_and_releases_cli_writer(reader):
    original = ValueError("body failed")
    with pytest.raises(ValueError) as caught:
        with reader._get_connection() as conn:
            conn.execute("INSERT INTO cpm_rows VALUES (1)")
            raise original
    assert caught.value is original
    with reader._get_connection() as reused:
        assert reused is conn
        assert not reused.in_transaction
    code = _cli_code(reader.db_path, """
        conn.execute('INSERT INTO cpm_rows VALUES (2)')
        conn.commit()
    """)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, timeout=60,
        **_subprocess_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert _count(reader) == 1


def test_t9b_uncommitted_work_is_discarded(reader):
    with reader._get_connection() as conn:
        conn.execute("INSERT INTO cpm_rows VALUES (1)")
    with reader._get_connection() as reused:
        assert reused is conn
        assert not reused.in_transaction
    assert _count(reader) == 0


def test_t9c_nested_checkout_preserves_outer_transaction(reader):
    with reader._get_connection() as outer:
        outer.execute("INSERT INTO cpm_rows VALUES (1)")
        with reader._get_connection() as inner:
            assert inner is outer
            inner.execute("INSERT INTO cpm_rows VALUES (2)")
        assert outer.in_transaction
        original = ValueError("inner failed")
        with pytest.raises(ValueError) as caught:
            with reader._get_connection():
                raise original
        assert caught.value is original
        assert outer.in_transaction
        outer.commit()
    assert _count(reader) == 2


def test_t10_path_switch_closes_only_calling_threads_old_connection(reader, tmp_path):
    second_path = tmp_path / "second.db"
    with closing(sqlite3.connect(second_path)) as conn:
        conn.execute("CREATE TABLE cpm_rows (value INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO cpm_rows VALUES (42)")
        conn.commit()
    with reader._get_connection() as old:
        assert _count(reader) == 0
    assert reader.set_database_path(str(second_path))
    with reader._get_connection() as new:
        assert new is not old
        assert new.execute("SELECT value FROM cpm_rows").fetchone()[0] == 42
    with pytest.raises(sqlite3.ProgrammingError):
        old.execute("SELECT 1")


def test_t11_old_style_connections_and_delete_rollback(reader):
    _insert(reader, 1)
    with closing(sqlite3.connect(reader.db_path)) as conn:
        assert conn.execute("SELECT value FROM cpm_rows").fetchone()[0] == 1
        conn.execute("INSERT INTO cpm_rows VALUES (2)")
        conn.commit()
    assert _count(reader) == 2
    reader.close_thread_connection()
    # Release the constructor's legacy, per-call schema-inspection connections too.
    gc.collect()
    with closing(sqlite3.connect(reader.db_path)) as conn:
        assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT value FROM cpm_rows ORDER BY value").fetchall() == [(1,), (2,)]


def test_t13_locked_error_is_original_exception(reader):
    original = sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError) as caught:
        with reader._get_connection():
            raise original
    assert caught.value is original
    with reader._get_connection() as conn:
        assert not conn.in_transaction


def test_t14_thread_churn_releases_connections(tmp_path, make_reader, monkeypatch):
    gc.collect()
    live = weakref.WeakSet()
    real_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        pass

    def connect(*args, **kwargs):
        conn = real_connect(*args, factory=TrackedConnection, **kwargs)
        live.add(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    reader = make_reader(bare=True)
    with reader._get_connection():
        pass
    baseline = len(live)
    assert baseline == 1
    barrier = threading.Barrier(12, timeout=10)

    def worker():
        with reader._get_connection() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
            barrier.wait(timeout=10)
        # Deliberately no explicit close: shutdown must release thread-local state.

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", ResourceWarning)
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(worker) for _ in range(12)]
            for future in futures:
                future.result(timeout=30)
        gc.collect()
    assert len(live) == baseline
    assert not [warning for warning in recorded if issubclass(warning.category, ResourceWarning)]


def test_checkout_timeout_changes_only_on_outermost_entry(reader):
    with reader._get_connection(timeout=5) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        with reader._get_connection(timeout=1) as nested:
            assert nested is conn
            assert nested.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    with reader._get_connection() as reused:
        assert reused is conn
        assert reused.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_rollback_failure_does_not_mask_body_exception(reader, monkeypatch):
    real_connect = sqlite3.connect

    class BrokenRollback(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("rollback failed")

    def connect(*args, **kwargs):
        return real_connect(*args, factory=BrokenRollback, **kwargs)

    reader.close_thread_connection()
    monkeypatch.setattr(sqlite3, "connect", connect)
    original = ValueError("body failed")
    with pytest.raises(ValueError) as caught:
        with reader._get_connection() as conn:
            conn.execute("INSERT INTO cpm_rows VALUES (1)")
            raise original
    assert caught.value is original
    assert reader._local.depth == 0
    reader.close_thread_connection()
    assert _count(reader) == 0


def test_helper_warns_without_stdout_and_preserves_other_pragmas(tmp_path, caplog, capsys):
    # An in-memory DB refuses WAL without raising and returns "memory" instead.
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=OFF")
        statements = []
        conn.set_trace_callback(statements.append)
        with caplog.at_level(logging.WARNING, logger="shared.db_connection"):
            assert configure_primary_connection(conn, busy_timeout_ms=1234) == "memory"
        assert statements == ["PRAGMA busy_timeout=1234", "PRAGMA journal_mode=WAL"]
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0
    assert len(caplog.records) == 1
    assert caplog.records[0].name == "shared.db_connection"
    assert "memory" in caplog.text
    assert capsys.readouterr().out == ""
