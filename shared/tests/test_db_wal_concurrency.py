"""CPM-B1/B2: primary DB pragmas, thread lifetime, and GUI/CLI concurrency."""

import ast
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


def test_rw_openers_use_helper():
    # CPM-B2 §5 plus the RA/PA-approved primary-DB functions. None guards
    # the whole file; explicit function sets keep B1 and legacy wipe out of scope.
    files = {
        "shared/database_ftp_persistence.py": None,
        "shared/database_http_persistence.py": None,
        "gui/utils/data_import_engine.py": None,
        "gui/utils/db_tools_engine_core_methods.py": None,
        "gui/utils/db_tools_engine_maintenance_methods.py": None,
        "gui/utils/db_tools_engine_merge_methods.py": None,
        "experimental/webui/db_actions.py": None,
        "experimental/webui/results_probe_actions.py": None,
        "experimental/webui/db.py": None,
        # Exempt analyze_database_schema: read-only validation of potentially
        # non-active candidate files must not convert their journal mode to WAL.
        "gui/utils/database_access_core_methods.py": {"_ensure_http_columns"},
        "experimental/redseek/store.py": {"init_db", "open_connection", "wipe_ingest_state"},
        "experimental/se_dork/store.py": {"init_db", "open_connection"},
    }
    checked = 0
    backup_outputs = 0
    for filename, functions in files.items():
        source = (REPO_ROOT / filename).read_text()
        tree = ast.parse(source)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "sqlite3"
                and call.func.attr == "connect"
            ):
                continue
            if "mode=ro" in ast.get_source_segment(source, call):
                continue
            function = parents[call]
            while not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = parents[function]
            if functions is not None and function.name not in functions:
                continue
            # The online-backup destination is a separate output, not a primary
            # DB opener in §5. Keep this exemption specific to this one call.
            if (
                filename == "gui/utils/db_tools_engine_core_methods.py"
                and function.name == "create_backup"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "backup_path"
            ):
                backup_outputs += 1
                continue
            location = f"{filename}:{call.lineno} ({function.name})"
            parent = parents[call]
            if isinstance(parent, ast.withitem):
                statement = parents[parent]
                assert isinstance(statement, ast.With), location
                connection = parent.optional_vars
                following = statement.body[0]
            else:
                assert isinstance(parent, ast.Assign) and len(parent.targets) == 1, location
                connection = parent.targets[0]
                block = parents[parent]
                siblings = next(
                    value for _, value in ast.iter_fields(block)
                    if isinstance(value, list) and parent in value
                )
                index = siblings.index(parent)
                assert index + 1 < len(siblings), location
                following = siblings[index + 1]
            # Permit immediate setup inside a try/finally cleanup block.
            if isinstance(following, ast.Try):
                assert following.finalbody, location
                following = following.body[0]
            assert isinstance(connection, ast.Name), location
            expected = ast.parse(f"configure_primary_connection({connection.id})").body[0]
            assert ast.dump(following) == ast.dump(expected), (
                f"{location}: missing immediate configure_primary_connection({connection.id})"
            )
            checked += 1
    assert checked == 28
    assert backup_outputs == 1


def test_webui_export_closes_connection_when_configuration_fails(tmp_path, monkeypatch):
    from experimental.webui import db

    path = tmp_path / "primary.db"
    conn = sqlite3.connect(path)
    original = sqlite3.OperationalError("configuration failed")

    def fail_configuration(opened):
        assert opened is conn
        raise original

    monkeypatch.setattr(db.sqlite3, "connect", lambda *args, **kwargs: conn)
    monkeypatch.setattr(db, "configure_primary_connection", fail_configuration)
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.export_db(path, tmp_path / "exports")
        assert caught.value is original
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("SELECT 1")
    finally:
        conn.close()


def test_t8d_http_persistence_and_gui_concurrent_writes(tmp_path, make_reader):
    # DatabaseReader initializes the real schema via run_migrations.
    reader = make_reader()
    ready = tmp_path / "http-ready"
    go = tmp_path / "http-go"
    code = textwrap.dedent(f"""
        from pathlib import Path
        import time
        from shared import db_migrations
        from shared.database_http_persistence import HttpPersistence

        # Isolate migration settings import from the developer's saved data.
        db_migrations._import_legacy_settings = lambda cur: None
        persistence = HttpPersistence({str(reader.db_path)!r})
        Path({str(ready)!r}).touch()
        deadline = time.monotonic() + 10
        while not Path({str(go)!r}).exists():
            assert time.monotonic() < deadline, 'parent start timed out'
            time.sleep(0.001)
        for value in range(1, 201):
            server_id = persistence.upsert_http_server(
                ip=f'192.0.2.{{value}}', country='Test', country_code='ZZ',
                port=80, scheme='http', banner='', title='', shodan_data='{{}}',
            )
            persistence.record_http_access(
                server_id=server_id, session_id=None, accessible=True,
                status_code=200, is_index_page=True, dir_count=1, file_count=2,
                tls_verified=False, error_message=None, access_details='{{}}',
            )
            time.sleep(0.001)
    """)
    barrier = threading.Barrier(5, timeout=10)

    def read():
        try:
            barrier.wait(timeout=10)
            counts = []
            for _ in range(200):
                with reader._get_connection() as conn:
                    counts.append(conn.execute("SELECT count(*) FROM http_servers").fetchone()[0])
                time.sleep(0.001)
            assert counts == sorted(counts)
        finally:
            reader.close_thread_connection()

    def write(offset):
        try:
            barrier.wait(timeout=10)
            for value in range(offset, offset + 100):
                with reader._get_connection() as conn:
                    conn.execute(
                        "INSERT INTO http_servers (ip_address, port) VALUES (?, ?)",
                        (f"198.51.100.{value}", 80),
                    )
                    conn.commit()
                time.sleep(0.001)
        finally:
            reader.close_thread_connection()

    with ThreadPoolExecutor(max_workers=5) as pool:
        process = pool.submit(
            subprocess.run, [sys.executable, "-c", code],
            timeout=60, capture_output=True, **_subprocess_kwargs(),
        )
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.done():
                result = process.result(timeout=1)
                pytest.fail(f"HTTP subprocess exited before ready: {result.stdout}\n{result.stderr}")
            assert time.monotonic() < deadline, "HTTP subprocess start timed out"
            time.sleep(0.001)
        futures = [pool.submit(read), pool.submit(read), pool.submit(write, 1), pool.submit(write, 101)]
        go.touch()
        barrier.wait(timeout=10)
        # Each result propagates parent reader/writer errors to the test.
        for future in futures:
            future.result(timeout=60)
        result = process.result(timeout=65)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "database is locked" not in result.stderr.lower()
    with reader._get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM http_servers").fetchone()[0] == 400
        assert conn.execute(
            "SELECT count(*) FROM http_servers WHERE ip_address LIKE '192.0.2.%'"
        ).fetchone()[0] == 200
        assert conn.execute(
            "SELECT count(*) FROM http_servers WHERE ip_address LIKE '198.51.100.%'"
        ).fetchone()[0] == 200
        assert conn.execute("SELECT count(*) FROM http_access").fetchone()[0] == 200
        assert conn.execute(
            "SELECT count(DISTINCT server_id) FROM http_access WHERE accessible=1 AND status_code=200"
        ).fetchone()[0] == 200


def test_t12_read_only_connection_during_immediate_write(reader):
    def read_only():
        start = time.monotonic()
        uri = Path(reader.db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as conn:
            count = conn.execute("SELECT count(*) FROM cpm_rows").fetchone()[0]
        return count, time.monotonic() - start

    with ThreadPoolExecutor(max_workers=1) as pool:
        with reader._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO cpm_rows VALUES (1)")
            count, elapsed = pool.submit(read_only).result(timeout=1)
            assert count == 0
            assert elapsed < 1
            assert conn.in_transaction
            conn.commit()
        count, elapsed = pool.submit(read_only).result(timeout=1)
        assert count == 1
        assert elapsed < 1
