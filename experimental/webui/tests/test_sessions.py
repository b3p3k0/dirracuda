"""Unit tests for SessionStore and cookie_name."""

from experimental.webui.sessions import SessionStore, cookie_name, COOKIE_NAME_PLAIN, COOKIE_NAME_SECURE


def test_create_returns_unique_ids():
    store = SessionStore()
    sid1, _ = store.create("alice")
    sid2, _ = store.create("alice")
    assert sid1 != sid2


def test_create_csrf_token_is_64_hex_chars():
    store = SessionStore()
    _, tok = store.create("alice")
    assert len(tok) == 64
    assert all(c in "0123456789abcdef" for c in tok)


def test_get_valid_session_within_timeouts():
    store = SessionStore()
    sid, _ = store.create("alice")
    s = store.get(sid, idle=1800, absolute=28800)
    assert s is not None
    assert s.username == "alice"


def test_get_unknown_id_returns_none():
    store = SessionStore()
    assert store.get("nonexistent", idle=1800, absolute=28800) is None


def test_idle_timeout_expires():
    store = SessionStore()
    sid, _ = store.create("alice")
    with store._lock:
        store._store[sid].last_accessed -= 1801
    assert store.get(sid, idle=1800, absolute=28800) is None


def test_absolute_timeout_expires():
    store = SessionStore()
    sid, _ = store.create("alice")
    with store._lock:
        store._store[sid].created_at -= 28801
    assert store.get(sid, idle=1800, absolute=28800) is None


def test_delete_removes_session():
    store = SessionStore()
    sid, _ = store.create("alice")
    store.delete(sid)
    assert store.get(sid, idle=1800, absolute=28800) is None


def test_delete_unknown_id_is_safe():
    store = SessionStore()
    store.delete("does-not-exist")


def test_cookie_name_tls_enabled():
    assert cookie_name(True) == COOKIE_NAME_SECURE
    assert cookie_name(True) == "__Host-dirracuda-session"


def test_cookie_name_tls_disabled():
    assert cookie_name(False) == COOKIE_NAME_PLAIN
    assert cookie_name(False) == "dirracuda-session"
