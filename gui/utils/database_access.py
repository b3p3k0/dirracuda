"""
Dirracuda Database Access Layer

Provides read-only access to the SQLite database with connection management,
caching, and thread-safe operations. Designed for GUI dashboard updates
and data browsing without interfering with backend operations.

Design Decision: Read-only access prevents any interference with backend
database operations while providing real-time dashboard updates.
"""

import sqlite3
import threading
import time
import json
import ipaddress
from typing import Dict, List, Optional, Any, Tuple, Set
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from shared.path_service import get_paths, get_legacy_paths, resolve_runtime_main_db_path
try:
    from error_codes import get_error, format_error_message
except ImportError:
    from .error_codes import get_error, format_error_message

_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)


class DatabaseReader:
    """
    Read-only database access for Dirracuda.

    Provides efficient, thread-safe access to the Dirracuda database with
    connection pooling, retry logic, and caching for dashboard updates.
    
    Design Pattern: Read-only with connection management to handle
    database locks when backend is writing during scans.
    """
    
    def __init__(
        self,
        db_path: str = str(resolve_runtime_main_db_path(paths=_PATHS, legacy=_LEGACY)),
        cache_duration: int = 5,
    ):
        """
        Initialize database reader.
        
        Args:
            db_path: Path to SQLite database file
            cache_duration: Cache duration in seconds for dashboard queries
            
        Design Decision: Short cache duration balances real-time updates
        with performance during dashboard refreshes.
        """
        self.db_path = Path(db_path).resolve()
        self.cache_duration = cache_duration
        self.cache = {}
        self.cache_timestamps = {}
        self.connection_lock = threading.Lock()

        try:
            from shared.db_migrations import run_migrations
            run_migrations(str(self.db_path))
        except Exception:
            pass

        # Ensure new RCE columns exist even on older databases (idempotent)
        try:
            self._ensure_rce_columns()
        except Exception:
            pass

        # Ensure legacy HTTP optional columns exist on older/minimal schemas.
        try:
            self._ensure_http_columns()
        except Exception:
            pass
        
        # Mock mode for testing
        self.mock_mode = False
        self.mock_data = self._get_mock_data()
        
        # Don't validate during initialization - let caller handle validation
        # self._validate_database()


from gui.utils.database_access_core_methods import bind_database_access_core_methods
from gui.utils.database_access_write_methods import bind_database_access_write_methods
from gui.utils.database_access_protocol_methods import bind_database_access_protocol_methods

_SHARED_BIND_SYMBOLS: Dict[str, Any] = {
    "get_error": get_error,
    "format_error_message": format_error_message,
}

bind_database_access_core_methods(DatabaseReader, _SHARED_BIND_SYMBOLS)
bind_database_access_write_methods(DatabaseReader, _SHARED_BIND_SYMBOLS)
bind_database_access_protocol_methods(DatabaseReader, _SHARED_BIND_SYMBOLS)
