"""
Unit tests for redseek/explorer_bridge.py

Groups:
  A — _infer_url: scheme, protocol field, port inference, fallback
  B — _ask_protocol: valid input, invalid input, cancel
  C — open_target: known URL, unknown prompt path, user cancel
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experimental.redseek.explorer_bridge import (
    _ask_protocol,
    _infer_url,
    _parse_for_internal,
    _show_fallback_dialog,
    open_target_system_browser,
    open_target,
    resolve_target_url,
)
from experimental.redseek.models import RedditTarget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _target(
    *,
    target_normalized: str = "",
    host: str | None = None,
    protocol: str | None = None,
) -> RedditTarget:
    return RedditTarget(
        id=1,
        post_id="abc",
        target_raw=target_normalized,
        target_normalized=target_normalized,
        host=host,
        protocol=protocol,
        notes=None,
        parse_confidence=None,
        created_at="2026-01-01 00:00:00",
        dedupe_key="x",
    )


# ---------------------------------------------------------------------------
# Group A — _infer_url
# ---------------------------------------------------------------------------

class TestInferUrl:

    def test_full_url_http(self):
        assert _infer_url(_target(target_normalized="http://example.com/files/")) == \
            "http://example.com/files/"

    def test_full_url_https(self):
        assert _infer_url(_target(target_normalized="https://example.com")) == \
            "https://example.com"

    def test_full_url_ftp(self):
        assert _infer_url(_target(target_normalized="ftp://files.example.com")) == \
            "ftp://files.example.com"

    def test_protocol_field_http(self):
        t = _target(protocol="http", host="10.0.0.1:8080")
        assert _infer_url(t) == "http://10.0.0.1:8080"

    def test_protocol_field_ftp(self):
        t = _target(protocol="ftp", host="ftp.example.com")
        assert _infer_url(t) == "ftp://ftp.example.com"

    def test_protocol_field_skipped_when_host_empty(self):
        # host=None — rule 2 guard must skip and fall through to None
        t = _target(protocol="http", host=None, target_normalized="bare.host")
        result = _infer_url(t)
        assert result is None

    def test_protocol_unknown_ignored(self):
        t = _target(protocol="unknown", host="example.com", target_normalized="example.com")
        assert _infer_url(t) is None

    def test_port_80_infers_http(self):
        t = _target(host="example.com:80", target_normalized="example.com:80")
        assert _infer_url(t) == "http://example.com:80"

    def test_port_443_infers_https(self):
        t = _target(host="example.com:443", target_normalized="example.com:443")
        assert _infer_url(t) == "https://example.com:443"

    def test_port_21_infers_ftp(self):
        t = _target(host="files.example.com:21", target_normalized="files.example.com:21")
        assert _infer_url(t) == "ftp://files.example.com:21"

    def test_bare_host_returns_none(self):
        t = _target(host="example.com", target_normalized="example.com")
        assert _infer_url(t) is None


# ---------------------------------------------------------------------------
# Group B — _ask_protocol
# ---------------------------------------------------------------------------

class TestAskProtocol:

    def test_valid_http_returned(self):
        with patch("experimental.redseek.explorer_bridge.simpledialog.askstring", return_value="http"):
            result = _ask_protocol(MagicMock(), "bare.host")
        assert result == "http"

    def test_valid_input_strips_scheme_suffix(self):
        # " https:// " should be normalised to "https"
        with patch("experimental.redseek.explorer_bridge.simpledialog.askstring", return_value=" https:// "):
            result = _ask_protocol(MagicMock(), "bare.host")
        assert result == "https"

    def test_cancel_returns_none(self):
        with patch("experimental.redseek.explorer_bridge.simpledialog.askstring", return_value=None):
            result = _ask_protocol(MagicMock(), "bare.host")
        assert result is None

    def test_empty_string_returns_none(self):
        with patch("experimental.redseek.explorer_bridge.simpledialog.askstring", return_value=""):
            result = _ask_protocol(MagicMock(), "bare.host")
        assert result is None

    def test_invalid_input_returns_none_and_shows_error(self):
        with patch("experimental.redseek.explorer_bridge.simpledialog.askstring", return_value="sftp"), \
             patch("experimental.redseek.explorer_bridge.messagebox.showerror") as mock_err:
            result = _ask_protocol(MagicMock(), "bare.host")
        assert result is None
        mock_err.assert_called_once()


# ---------------------------------------------------------------------------
# Group C — open_target
# ---------------------------------------------------------------------------

class TestOpenTarget:

    # -- renamed from test_known_url_opens_directly --
    # Retains proof that URL inference works; asserts new internal-first behavior.
    def test_known_url_no_factory_shows_fallback(self):
        t = _target(target_normalized="https://example.com/files/")
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="cancel") as mock_dlg, \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock())
        mock_dlg.assert_called_once()
        _, call_url, _ = mock_dlg.call_args[0]
        assert call_url == "https://example.com/files/"
        mock_open.assert_not_called()

    def test_unknown_target_calls_ask_protocol(self):
        t = _target(host="bare.host", target_normalized="bare.host")
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value=None) as mock_ask, \
             patch("experimental.redseek.explorer_bridge.webbrowser.open"):
            open_target(t, MagicMock())
        mock_ask.assert_called_once()

    def test_user_cancel_does_not_open_browser(self):
        t = _target(host="bare.host", target_normalized="bare.host")
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value=None), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock())
        mock_open.assert_not_called()

    # -- renamed from test_prompt_protocol_constructs_url --
    # Retains proof that host/port construction is correct; asserts new factory path.
    def test_prompt_protocol_then_internal_launch(self):
        t = _target(host="bare.host", target_normalized="bare.host")
        factory = MagicMock()
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value="http"), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock(), browser_factory=factory)
        factory.assert_called_once_with("http", "bare.host", 80, start_path="/")
        mock_open.assert_not_called()

    def test_internal_launch_success(self):
        t = _target(target_normalized="ftp://files.example.com")
        factory = MagicMock()
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog") as mock_dlg, \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock(), browser_factory=factory)
        factory.assert_called_once_with("ftp", "files.example.com", 21, start_path="/")
        mock_dlg.assert_not_called()
        mock_open.assert_not_called()

    def test_internal_launch_success_passes_subpath_for_http(self):
        t = _target(target_normalized="https://example.com/movies/")
        factory = MagicMock()
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog") as mock_dlg, \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock(), browser_factory=factory)
        factory.assert_called_once_with("https", "example.com", 443, start_path="/movies/")
        mock_dlg.assert_not_called()
        mock_open.assert_not_called()

    def test_no_factory_shows_fallback(self):
        t = _target(target_normalized="http://example.com")
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="cancel") as mock_dlg:
            open_target(t, MagicMock())
        mock_dlg.assert_called_once()
        _, _, reason = mock_dlg.call_args[0]
        assert "not available" in reason.lower()

    def test_factory_failure_shows_fallback(self):
        t = _target(target_normalized="http://example.com")
        factory = MagicMock(side_effect=RuntimeError("conn refused"))
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="cancel") as mock_dlg:
            open_target(t, MagicMock(), browser_factory=factory)
        mock_dlg.assert_called_once()
        _, _, reason = mock_dlg.call_args[0]
        assert "conn refused" in reason

    def test_fallback_browser_opens_url(self):
        t = _target(target_normalized="http://example.com")
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="browser"), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock())
        mock_open.assert_called_once_with("http://example.com")

    def test_fallback_copy_uses_clipboard(self):
        t = _target(target_normalized="http://example.com")
        parent = MagicMock()
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="copy"):
            open_target(t, parent)
        parent.clipboard_clear.assert_called_once()
        parent.clipboard_append.assert_called_once_with("http://example.com")

    def test_fallback_cancel_is_silent(self):
        t = _target(target_normalized="http://example.com")
        parent = MagicMock()
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="cancel"), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, parent)
        mock_open.assert_not_called()
        parent.clipboard_clear.assert_not_called()
        parent.clipboard_append.assert_not_called()

    def test_unsupported_scheme_shows_fallback(self):
        # protocol="smb" + host triggers Rule 2 of _infer_url -> "smb://fileserver"
        t = _target(protocol="smb", host="fileserver", target_normalized="fileserver")
        with patch("experimental.redseek.explorer_bridge._show_fallback_dialog", return_value="cancel") as mock_dlg, \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target(t, MagicMock())
        mock_dlg.assert_called_once()
        _, _, reason = mock_dlg.call_args[0]
        assert "ftp" in reason.lower() or "http" in reason.lower()
        mock_open.assert_not_called()


class TestSystemBrowserHelpers:

    def test_resolve_target_url_known_scheme(self):
        t = _target(target_normalized="https://example.com/a")
        assert resolve_target_url(t, MagicMock()) == "https://example.com/a"

    def test_resolve_target_url_prompt_cancel_returns_none(self):
        t = _target(target_normalized="bare.host", host="bare.host")
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value=None):
            assert resolve_target_url(t, MagicMock()) is None

    def test_open_target_system_browser_opens_known_url(self):
        t = _target(target_normalized="http://example.com")
        with patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target_system_browser(t, MagicMock())
        mock_open.assert_called_once_with("http://example.com")

    def test_open_target_system_browser_prompt_then_open(self):
        t = _target(target_normalized="bare.host", host="bare.host")
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value="https"), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target_system_browser(t, MagicMock())
        mock_open.assert_called_once_with("https://bare.host")

    def test_open_target_system_browser_cancel_silent(self):
        t = _target(target_normalized="bare.host", host="bare.host")
        with patch("experimental.redseek.explorer_bridge._ask_protocol", return_value=None), \
             patch("experimental.redseek.explorer_bridge.webbrowser.open") as mock_open:
            open_target_system_browser(t, MagicMock())
        mock_open.assert_not_called()
