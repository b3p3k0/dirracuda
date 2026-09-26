"""Database helpers shared by the Web UI's results and detail queries.

A third module rather than helpers left in ``db.py``: ``db.py`` re-exports
``get_result_details`` so ``app.py`` keeps resolving it there, and having
``db_details`` import helpers back from ``db.py`` would close that into an
import cycle.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path.home() / ".dirracuda" / "data" / "dirracuda.db"
_DEFAULT_DB_PATH = DEFAULT_DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a read-only URI connection. Raises OperationalError if DB absent or locked."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _inspect_tables(conn: sqlite3.Connection) -> set:
    """Return set of table names present in the DB."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _inspect_columns(conn: sqlite3.Connection, table: str) -> set:
    """Return set of column names for *table*. table must be a literal from our code."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _table_has_columns(table_columns: dict[str, set[str]], table: str, *columns: str) -> bool:
    cols = table_columns.get(table)
    if not cols:
        return False
    return all(col in cols for col in columns)


def _format_last_seen(value: Optional[str]) -> str:
    if not value:
        return "Never"
    text = str(value).strip()
    if not text:
        return "Never"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
