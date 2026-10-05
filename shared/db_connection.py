"""Connection configuration shared by primary database openers."""

import logging
import sqlite3
import weakref


PRIMARY_DB_BUSY_TIMEOUT_MS = 30000


class ThreadConnectionCleanup:
    """Close a connection when its owning thread's cached state is released."""

    def __init__(self, conn: sqlite3.Connection):
        self.close = weakref.finalize(self, conn.close)


def configure_primary_connection(
    conn: sqlite3.Connection, *, busy_timeout_ms: int = PRIMARY_DB_BUSY_TIMEOUT_MS
) -> str:
    """Set the busy wait and request WAL, tolerating a different returned mode."""
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
    if mode != "wal":
        logging.getLogger(__name__).warning(
            "Primary database refused WAL; using journal mode %s", mode
        )
    return mode
