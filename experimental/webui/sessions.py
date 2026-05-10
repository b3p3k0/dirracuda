"""Server-side session store for the web UI.

In-memory only. Sessions do not survive server restart.
Single-process, single-worker only in v1.
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

SESSION_ID_BYTES = 32   # 256 bits of entropy
CSRF_TOKEN_BYTES = 32   # 256 bits of entropy

COOKIE_NAME_SECURE = "__Host-dirracuda-session"
COOKIE_NAME_PLAIN = "dirracuda-session"


@dataclass
class Session:
    username: str
    csrf_token: str
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self) -> None:
        self._store: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> tuple:
        """Return (session_id, csrf_token) for a new session."""
        sid = secrets.token_hex(SESSION_ID_BYTES)
        tok = secrets.token_hex(CSRF_TOKEN_BYTES)
        with self._lock:
            self._store[sid] = Session(username=username, csrf_token=tok)
        return sid, tok

    def get(self, sid: str, idle: int, absolute: int) -> Optional[Session]:
        """Return session if valid, enforcing idle and absolute timeouts. None otherwise."""
        with self._lock:
            s = self._store.get(sid)
            if s is None:
                return None
            now = time.time()
            if now - s.last_accessed > idle or now - s.created_at > absolute:
                del self._store[sid]
                return None
            s.last_accessed = now
            return s

    def delete(self, sid: str) -> None:
        with self._lock:
            self._store.pop(sid, None)


def cookie_name(tls_enabled: bool) -> str:
    return COOKIE_NAME_SECURE if tls_enabled else COOKIE_NAME_PLAIN
