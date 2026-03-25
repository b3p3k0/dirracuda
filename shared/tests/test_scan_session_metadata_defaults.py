"""
Tests for F5 scan session metadata transitions.

Covers:
- Fresh migration defaults for scan_sessions.
- Legacy ensure/backfill safety for tool_name.
- tools/db_schema.sql default parity.
- Unified workflow session label arguments.
- Explicit tool_name/scan_type session writes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from shared.database import SMBSeekWorkflowDatabase
from shared.db_migrations import run_migrations
from shared.workflow import UnifiedWorkflow


def _scan_sessions_default(conn: sqlite3.Connection, column_name: str) -> str:
    row = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('scan_sessions') WHERE name = ?",
        (column_name,),
    ).fetchone()
    assert row is not None
    return row[0]


def test_run_migrations_fresh_sets_canonical_tool_name_default(tmp_path) -> None:
    db_path = tmp_path / "f5_fresh.db"
    run_migrations(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        assert _scan_sessions_default(conn, "tool_name") == "'dirracuda'"
        assert _scan_sessions_default(conn, "scan_type") == "'smbseek_unified'"

        conn.execute("INSERT INTO scan_sessions DEFAULT VALUES")
        row = conn.execute(
            "SELECT tool_name, scan_type FROM scan_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row == ("dirracuda", "smbseek_unified")
    finally:
        conn.close()


def test_run_migrations_legacy_ensure_path_keeps_smbseek_backfill(tmp_path) -> None:
    db_path = tmp_path / "f5_legacy.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE scan_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_type TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO scan_sessions (scan_type) VALUES ('discover');
            INSERT INTO scan_sessions (scan_type) VALUES (NULL);
            """
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        assert _scan_sessions_default(conn, "tool_name") == "'smbseek'"
        rows = conn.execute(
            "SELECT tool_name, scan_type FROM scan_sessions ORDER BY id"
        ).fetchall()
        assert rows[0] == ("smbseek", "discover")
        assert rows[1] == ("smbseek", "smbseek_unified")
    finally:
        conn.close()


def test_tools_schema_sets_canonical_tool_name_default(tmp_path) -> None:
    db_path = tmp_path / "f5_tools_schema.db"
    schema_path = Path(__file__).resolve().parents[2] / "tools" / "db_schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema_sql)
        assert _scan_sessions_default(conn, "tool_name") == "'dirracuda'"
    finally:
        conn.close()


class _WorkflowConfigStub:
    def validate_configuration(self) -> bool:
        return True


class _WorkflowOutputStub:
    def print_if_verbose(self, _msg: str) -> None:
        pass

    def header(self, _msg: str) -> None:
        pass

    def info(self, _msg: str) -> None:
        pass

    def warning(self, _msg: str) -> None:
        pass

    def success(self, _msg: str) -> None:
        pass

    def error(self, _msg: str) -> None:
        pass


class _WorkflowDatabaseStub:
    def __init__(self) -> None:
        self.calls = []

    def show_database_status(self) -> None:
        pass

    def create_session(self, tool_name: str, scan_type: Optional[str] = None) -> int:
        self.calls.append((tool_name, scan_type))
        return 42

    def close(self) -> None:
        pass


def test_unified_workflow_writes_canonical_session_labels() -> None:
    db_stub = _WorkflowDatabaseStub()
    workflow = UnifiedWorkflow(
        _WorkflowConfigStub(),
        _WorkflowOutputStub(),
        db_stub,
        cautious_mode=False,
    )
    workflow._execute_discovery = lambda _args: SimpleNamespace(  # type: ignore[method-assign]
        host_ips=[],
        query_used="test",
        total_hosts=0,
    )

    summary = workflow.run(SimpleNamespace())
    assert db_stub.calls == [("dirracuda", "smbseek_unified")]
    assert summary.session_id == 42


class _DatabaseConfigStub:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self.config = {}

    def get_database_path(self) -> str:
        return self._db_path


def test_create_session_supports_explicit_scan_type(tmp_path) -> None:
    db_path = tmp_path / "f5_create_session.db"
    db = SMBSeekWorkflowDatabase(_DatabaseConfigStub(str(db_path)))
    try:
        session_id = db.create_session("dirracuda", scan_type="smbseek_unified")
        row = db.db_manager.execute_query(
            "SELECT tool_name, scan_type FROM scan_sessions WHERE id = ?",
            (session_id,),
        )[0]
        assert row["tool_name"] == "dirracuda"
        assert row["scan_type"] == "smbseek_unified"
    finally:
        db.close()
