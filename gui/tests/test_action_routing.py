"""
Card 5: Action routing and protocol isolation tests.

Tests the batch mixin methods without a real Tkinter window by constructing
a minimal stub object that mixes in the relevant classes.

Covers:
- _server_data_to_target: row_key + host_type propagation and defaults
- _handle_probe_status_update: row_key-keyed match; no S+F cross-bleed
- _handle_rce_status_update: same isolation pattern
- _handle_extracted_update: row_key match; uses upsert_extracted_flag_for_host
- delete routing: SMB-only, FTP-only, mixed; probe cache cleared for SMB only
- _execute_probe_target: F row runs FTP probe/units=1; S row returns units=1
- _execute_extract_target: F/H rows route through protocol extract runner
- _launch_browse_workflow: F row opens FtpBrowserWindow, not FileBrowserWindow
- probe progress per-target invariant: mixed S+F batch completes correctly
- _attach_probe_status: F row uses DB value, never calls _determine_probe_status
- _on_pry_selected: F row shows warning, pry never launched
"""

import importlib
import sys
import threading
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_batch_mixins_isolated():
    """
    Import batch mixins without executing gui/components/server_list_window/__init__.py,
    which pulls in window.py and optional impacket dependencies.
    """
    sentinel = object()
    prior_modules = {}
    module_names = [
        "gui.components.pry_dialog",
        "gui.components.pry_status_dialog",
        "gui.components.batch_extract_dialog",
        "gui.components.server_list_window",
        "gui.components.server_list_window.export",
        "gui.components.server_list_window.details",
        "gui.components.server_list_window.filters",
        "gui.components.server_list_window.table",
        "gui.components.server_list_window.actions",
        "gui.components.server_list_window.actions.batch_operations",
        "gui.components.server_list_window.actions.batch_status",
        "gui.components.server_list_window.actions.batch",
    ]
    for name in module_names:
        prior_modules[name] = sys.modules.get(name, sentinel)

    slw_dir = Path(__file__).resolve().parents[1] / "components" / "server_list_window"
    actions_dir = slw_dir / "actions"

    try:
        for name in module_names:
            sys.modules.pop(name, None)

        def _stub_module(name: str, attrs: Dict[str, Any]) -> None:
            mod = types.ModuleType(name)
            for k, v in attrs.items():
                setattr(mod, k, v)
            sys.modules[name] = mod

        # Lightweight GUI stubs for optional runtime dependencies.
        _stub_module("gui.components.pry_dialog", {"PryDialog": type("PryDialog", (), {})})
        _stub_module(
            "gui.components.pry_status_dialog",
            {"BatchStatusDialog": type("BatchStatusDialog", (), {})},
        )
        _stub_module(
            "gui.components.batch_extract_dialog",
            {"BatchExtractSettingsDialog": type("BatchExtractSettingsDialog", (), {})},
        )

        # Package stub to bypass server_list_window/__init__.py side effects.
        slw_pkg = types.ModuleType("gui.components.server_list_window")
        slw_pkg.__path__ = [str(slw_dir)]
        sys.modules["gui.components.server_list_window"] = slw_pkg

        # Minimal sibling modules expected by batch imports.
        export_mod = types.ModuleType("gui.components.server_list_window.export")
        details_mod = types.ModuleType("gui.components.server_list_window.details")
        filters_mod = types.ModuleType("gui.components.server_list_window.filters")
        table_mod = types.ModuleType("gui.components.server_list_window.table")
        details_mod._derive_credentials = lambda _auth: ("", "")

        def _get_selected_server_data(tree, filtered_servers):
            selected = set(tree.selection())
            return [s for s in filtered_servers if s.get("row_key") in selected]

        table_mod.get_selected_server_data = _get_selected_server_data

        sys.modules["gui.components.server_list_window.export"] = export_mod
        sys.modules["gui.components.server_list_window.details"] = details_mod
        sys.modules["gui.components.server_list_window.filters"] = filters_mod
        sys.modules["gui.components.server_list_window.table"] = table_mod
        slw_pkg.export = export_mod
        slw_pkg.details = details_mod
        slw_pkg.filters = filters_mod
        slw_pkg.table = table_mod

        actions_pkg = types.ModuleType("gui.components.server_list_window.actions")
        actions_pkg.__path__ = [str(actions_dir)]
        sys.modules["gui.components.server_list_window.actions"] = actions_pkg

        batch_operations_mod = importlib.import_module("gui.components.server_list_window.actions.batch_operations")
        batch_status_mod = importlib.import_module("gui.components.server_list_window.actions.batch_status")
        batch_mod = importlib.import_module("gui.components.server_list_window.actions.batch")

        return (
            batch_operations_mod.ServerListWindowBatchOperationsMixin,
            batch_status_mod.ServerListWindowBatchStatusMixin,
            batch_mod.ServerListWindowBatchMixin,
        )
    finally:
        for name, previous in prior_modules.items():
            if previous is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


(
    ServerListWindowBatchOperationsMixin,
    ServerListWindowBatchStatusMixin,
    ServerListWindowBatchMixin,
) = _import_batch_mixins_isolated()


# ---------------------------------------------------------------------------
# Minimal stub harness
# ---------------------------------------------------------------------------

class _StubWindow:
    """Minimal Tk window substitute."""
    def after(self, ms, fn, *args):
        fn(*args)

    def clipboard_clear(self): pass
    def clipboard_append(self, v): pass


class _StubTree:
    def __init__(self):
        self._selection = []
        self._items = {}
        self._focused = None

    def selection(self):
        return list(self._selection)

    def exists(self, iid):
        return iid in self._items

    def selection_add(self, iid):
        if iid not in self._selection:
            self._selection.append(iid)

    def selection_set(self, iids):
        if isinstance(iids, (list, tuple, set)):
            self._selection = list(iids)
        else:
            self._selection = [iids]

    def selection_remove(self, iids):
        if isinstance(iids, (list, tuple, set)):
            remove_set = set(iids)
        else:
            remove_set = {iids}
        self._selection = [iid for iid in self._selection if iid not in remove_set]

    def focus(self, iid):
        self._focused = iid

    def see(self, iid):
        self._focused = iid


class _StubSettingsManager:
    def __init__(self):
        self._probe = {}

    def set_probe_status(self, ip, status):
        self._probe[ip] = status

    def get_probe_status(self, ip):
        return self._probe.get(ip, "unprobed")

    def get_setting(self, key, default=None):
        return default

    def get_smbseek_config_path(self):
        return None


class _BatchMixinStub(ServerListWindowBatchMixin):
    """Minimal concrete class mixing in both batch mixins."""

    def __init__(self, db_reader=None):
        self.window = _StubWindow()
        self.tree = _StubTree()
        self.db_reader = db_reader or MagicMock()
        self.settings_manager = _StubSettingsManager()
        self.theme = MagicMock()
        self.status_label = None
        self.probe_button = None
        self.extract_button = None
        self.pry_button = None
        self.browser_button = None
        self.delete_button = None
        self.stop_button = None
        self.details_button = None
        self.pry_status_button = None
        self.context_menu = None
        self.batch_status_dialog = None
        self.active_jobs: Dict[str, Any] = {}
        self.all_servers: List[Dict[str, Any]] = []
        self.filtered_servers: List[Dict[str, Any]] = []
        self.indicator_patterns = None
        self.probe_status_map: Dict[str, str] = {}
        self._delete_in_progress = False
        self._pending_table_refresh = False
        self._pending_selection: List[str] = []
        self._context_menu_visible = False
        self._context_menu_bindings: List = []
        self._delete_menu_index = None
        self.is_advanced_mode = False
        self.filter_frame = MagicMock()
        self.table_frame = MagicMock()
        self.filter_widgets: Dict = {}
        self.mode_button = MagicMock()
        self.search_text = MagicMock()
        self.country_listbox = None
        self._pry_unlocked = True
        self._rce_unlocked = True

    def _apply_filters(self, force=False):
        pass

    def _load_data(self):
        pass

    def _set_table_interaction_enabled(self, enabled: bool):
        pass

    def _is_table_lock_required(self, job_type: str) -> bool:
        return False

    def _get_config_path(self) -> Optional[str]:
        return None


# ---------------------------------------------------------------------------
# _server_data_to_target
# ---------------------------------------------------------------------------


def test_server_data_to_target_propagates_row_key_and_host_type():
    stub = _BatchMixinStub()
    server_data = {
        "ip_address": "1.2.3.4",
        "auth_method": "anonymous",
        "row_key": "S:42",
        "host_type": "S",
        "accessible_shares_list": "docs,data",
    }
    target = stub._server_data_to_target(server_data)
    assert target["row_key"] == "S:42"
    assert target["host_type"] == "S"
    assert target["ip_address"] == "1.2.3.4"


def test_server_data_to_target_defaults_host_type_s():
    stub = _BatchMixinStub()
    server_data = {"ip_address": "2.3.4.5"}
    target = stub._server_data_to_target(server_data)
    assert target["host_type"] == "S"
    assert target["row_key"] is None


# ---------------------------------------------------------------------------
# _handle_probe_status_update
# ---------------------------------------------------------------------------


def _make_two_siblings(ip="1.2.3.4") -> List[Dict[str, Any]]:
    """Return [S-row, F-row] for the same IP with distinct row_keys."""
    return [
        {"ip_address": ip, "host_type": "S", "row_key": "S:1",
         "probe_status": "unprobed", "probe_status_emoji": "○"},
        {"ip_address": ip, "host_type": "F", "row_key": "F:2",
         "probe_status": "unprobed", "probe_status_emoji": "○"},
    ]


def test_probe_status_update_by_row_key_matches_only_correct_row():
    stub = _BatchMixinStub()
    stub.all_servers = _make_two_siblings()

    stub._handle_probe_status_update("1.2.3.4", "clean", row_key="S:1")

    s_row = next(r for r in stub.all_servers if r["host_type"] == "S")
    f_row = next(r for r in stub.all_servers if r["host_type"] == "F")
    assert s_row["probe_status"] == "clean"
    assert f_row["probe_status"] == "unprobed", "F sibling must not be updated"


def test_probe_status_update_no_row_key_smb_only_fallback():
    """Without row_key, only SMB rows are touched (legacy fallback)."""
    stub = _BatchMixinStub()
    stub.all_servers = _make_two_siblings()

    stub._handle_probe_status_update("1.2.3.4", "issue")  # no row_key

    s_row = next(r for r in stub.all_servers if r["host_type"] == "S")
    f_row = next(r for r in stub.all_servers if r["host_type"] == "F")
    assert s_row["probe_status"] == "issue"
    assert f_row["probe_status"] == "unprobed"


# ---------------------------------------------------------------------------
# _handle_rce_status_update
# ---------------------------------------------------------------------------


def test_rce_status_update_by_row_key_matches_only_correct_row():
    stub = _BatchMixinStub()
    stub.all_servers = [
        {"ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:1",
         "rce_status": "not_run", "rce_status_emoji": "⭘"},
        {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:2",
         "rce_status": "not_run", "rce_status_emoji": "⭘"},
    ]

    stub._handle_rce_status_update("1.2.3.4", "flagged", row_key="S:1")

    s_row = next(r for r in stub.all_servers if r["host_type"] == "S")
    f_row = next(r for r in stub.all_servers if r["host_type"] == "F")
    assert s_row["rce_status"] == "flagged"
    assert f_row["rce_status"] == "not_run", "F sibling must not be updated"


# ---------------------------------------------------------------------------
# _handle_extracted_update
# ---------------------------------------------------------------------------


def test_extracted_update_uses_host_type_for_db_write():
    """Verifies upsert_extracted_flag_for_host(ip, 'S', True) is called, not the shim."""
    mock_db = MagicMock()
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.all_servers = []

    stub._handle_extracted_update("1.2.3.4", row_key=None, host_type="S")

    mock_db.upsert_extracted_flag_for_host.assert_called_once_with("1.2.3.4", "S", True)
    mock_db.upsert_extracted_flag.assert_not_called()


def test_extracted_update_by_row_key_matches_only_correct_row():
    """Same IP in both rows; extracted update by row_key touches only target row."""
    stub = _BatchMixinStub()
    stub.all_servers = [
        {"ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:1",
         "extracted": 0, "extract_status_emoji": "○"},
        {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:2",
         "extracted": 0, "extract_status_emoji": "○"},
    ]

    stub._handle_extracted_update("1.2.3.4", row_key="S:1", host_type="S")

    s_row = next(r for r in stub.all_servers if r["host_type"] == "S")
    f_row = next(r for r in stub.all_servers if r["host_type"] == "F")
    assert s_row["extracted"] == 1
    assert f_row["extracted"] == 0, "F sibling must not be marked extracted"


# ---------------------------------------------------------------------------
# Delete routing
# ---------------------------------------------------------------------------


def test_delete_smb_row_does_not_delete_ftp_sibling():
    """row_spec ('S', ip) → bulk_delete_rows called with SMB spec only."""
    mock_db = MagicMock()
    mock_db.bulk_delete_rows.return_value = {
        "deleted_count": 1, "deleted_ips": ["1.2.3.4"],
        "deleted_smb_ips": ["1.2.3.4"], "error": None,
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    result = stub._run_delete_operation([("S", "1.2.3.4")])

    mock_db.bulk_delete_rows.assert_called_once_with([("S", "1.2.3.4")])
    assert result["deleted_smb_ips"] == ["1.2.3.4"]


def test_delete_ftp_row_does_not_delete_smb_sibling():
    """row_spec ('F', ip) → bulk_delete_rows called with FTP spec; deleted_smb_ips empty."""
    mock_db = MagicMock()
    mock_db.bulk_delete_rows.return_value = {
        "deleted_count": 1, "deleted_ips": ["1.2.3.4"],
        "deleted_smb_ips": [], "error": None,
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    result = stub._run_delete_operation([("F", "1.2.3.4")])

    mock_db.bulk_delete_rows.assert_called_once_with([("F", "1.2.3.4")])
    assert result["deleted_smb_ips"] == []


def test_mixed_selection_delete_partitions_by_protocol():
    """S+F row_specs → bulk_delete_rows receives both; no legacy file cache clear side effects."""
    mock_db = MagicMock()
    mock_db.bulk_delete_rows.return_value = {
        "deleted_count": 2,
        "deleted_ips": ["1.2.3.4", "5.6.7.8"],
        "deleted_smb_ips": ["1.2.3.4"],
        "error": None,
    }
    stub = _BatchMixinStub(db_reader=mock_db)

    cleared_ips = []
    with patch("gui.utils.probe_cache.clear_probe_result", side_effect=lambda ip: cleared_ips.append(ip)):
        stub._run_delete_operation([("S", "1.2.3.4"), ("F", "5.6.7.8")])

    assert cleared_ips == [], "Delete flow should not clear legacy probe cache files"


def test_delete_ftp_only_does_not_clear_smb_probe_cache():
    """FTP-only delete produces empty deleted_smb_ips → no probe cache cleared."""
    mock_db = MagicMock()
    mock_db.bulk_delete_rows.return_value = {
        "deleted_count": 1, "deleted_ips": ["9.9.9.9"],
        "deleted_smb_ips": [], "error": None,
    }
    stub = _BatchMixinStub(db_reader=mock_db)

    cleared_ips = []
    with patch("gui.utils.probe_cache.clear_probe_result", side_effect=lambda ip: cleared_ips.append(ip)):
        stub._run_delete_operation([("F", "9.9.9.9")])

    assert cleared_ips == [], "No probe cache clear for FTP-only delete"


# ---------------------------------------------------------------------------
# _on_add_record
# ---------------------------------------------------------------------------


def test_add_record_success_refreshes_and_selects_visible_row():
    mock_db = MagicMock()
    mock_db.upsert_manual_server_record.return_value = {
        "host_type": "S",
        "protocol_server_id": 42,
        "row_key": "S:42",
        "operation": "insert",
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {"S:42": True}
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "S",
        "ip_address": "1.2.3.4",
    })
    stub._load_data = MagicMock()
    stub._apply_filters = MagicMock()
    stub._set_status = MagicMock()

    stub._on_add_record()

    mock_db.upsert_manual_server_record.assert_called_once_with({
        "host_type": "S",
        "ip_address": "1.2.3.4",
    })
    mock_db.clear_cache.assert_called_once()
    stub._load_data.assert_called_once()
    stub._apply_filters.assert_called_once_with(force=True)
    assert stub.tree.selection() == ["S:42"]
    assert "SMB record insert" in stub._set_status.call_args[0][0]


def test_add_record_hidden_by_filters_sets_explicit_note():
    mock_db = MagicMock()
    mock_db.upsert_manual_server_record.return_value = {
        "host_type": "F",
        "protocol_server_id": 7,
        "row_key": "F:7",
        "operation": "update",
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {}  # row not visible in current filtered table
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "F",
        "ip_address": "5.6.7.8",
    })
    stub._load_data = MagicMock()
    stub._apply_filters = MagicMock()
    stub._set_status = MagicMock()

    stub._on_add_record()

    stub._apply_filters.assert_called_once_with(force=True)
    status_msg = stub._set_status.call_args[0][0]
    assert "hidden by current filters" in status_msg
    assert "Shares > 0" in status_msg


def test_add_record_probe_enabled_runs_before_upsert_and_persists_cache():
    mock_db = MagicMock()
    upsert_result = {
        "host_type": "H",
        "protocol_server_id": 99,
        "row_key": "H:99",
        "operation": "insert",
    }
    mock_db.upsert_manual_server_record.return_value = upsert_result
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {"H:99": True}
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "H",
        "ip_address": "1.2.3.4",
        "port": 80,
        "scheme": "http",
        "_probe_before_add": True,
    })
    call_order: list[str] = []
    stub._run_manual_add_probe = MagicMock(side_effect=lambda payload: (
        call_order.append("probe"),
        {
            "status": "clean",
            "indicator_matches": 0,
            "snapshot_path": "/tmp/snap.json",
            "accessible_dirs_count": 1,
            "accessible_dirs_list": "/",
            "accessible_files_count": 0,
            "port": 80,
        },
    )[1])

    def _upsert(payload):
        call_order.append("upsert")
        return upsert_result
    mock_db.upsert_manual_server_record.side_effect = _upsert

    stub._on_add_record()

    assert call_order == ["probe", "upsert"]
    stub._run_manual_add_probe.assert_called_once()
    called_payload = stub._run_manual_add_probe.call_args[0][0]
    assert "_probe_before_add" not in called_payload
    mock_db.upsert_probe_cache_for_host.assert_called_once()
    kwargs = mock_db.upsert_probe_cache_for_host.call_args.kwargs
    assert kwargs["status"] == "clean"
    assert kwargs["protocol_server_id"] == 99


def test_add_record_probe_disabled_skips_probe():
    mock_db = MagicMock()
    mock_db.upsert_manual_server_record.return_value = {
        "host_type": "S",
        "protocol_server_id": 42,
        "row_key": "S:42",
        "operation": "insert",
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {"S:42": True}
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "S",
        "ip_address": "1.2.3.4",
        "_probe_before_add": False,
    })
    stub._run_manual_add_probe = MagicMock()

    stub._on_add_record()

    stub._run_manual_add_probe.assert_not_called()
    mock_db.upsert_probe_cache_for_host.assert_not_called()
    mock_db.upsert_manual_server_record.assert_called_once_with({
        "host_type": "S",
        "ip_address": "1.2.3.4",
    })


def test_add_record_probe_failure_warns_and_still_saves(monkeypatch):
    mock_db = MagicMock()
    mock_db.upsert_manual_server_record.return_value = {
        "host_type": "F",
        "protocol_server_id": 7,
        "row_key": "F:7",
        "operation": "insert",
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {"F:7": True}
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "F",
        "ip_address": "5.6.7.8",
        "port": 21,
        "_probe_before_add": True,
    })
    stub._run_manual_add_probe = MagicMock(side_effect=RuntimeError("timed out"))
    warnings = []
    monkeypatch.setattr(
        "tkinter.messagebox.showwarning",
        lambda *a, **k: warnings.append((a, k)),
    )

    stub._on_add_record()

    assert len(warnings) == 1
    mock_db.upsert_manual_server_record.assert_called_once()
    mock_db.upsert_probe_cache_for_host.assert_not_called()


def test_add_record_probe_flag_not_passed_to_db_payload():
    mock_db = MagicMock()
    mock_db.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 3,
        "row_key": "H:3",
        "operation": "insert",
    }
    stub = _BatchMixinStub(db_reader=mock_db)
    stub.tree._items = {"H:3": True}
    stub._show_add_record_dialog = MagicMock(return_value={
        "host_type": "H",
        "ip_address": "9.8.7.6",
        "port": 8080,
        "scheme": "http",
        "_probe_before_add": False,
    })

    stub._on_add_record()

    sent = mock_db.upsert_manual_server_record.call_args[0][0]
    assert "_probe_before_add" not in sent


# ---------------------------------------------------------------------------
# _execute_probe_target
# ---------------------------------------------------------------------------


def test_probe_ftp_row_runs_and_persists():
    stub = _BatchMixinStub()
    target = {
        "ip_address": "1.2.3.4",
        "host_type": "F",
        "row_key": "F:7",
        "shares": [],
        "data": {"port": 21},
    }

    import gui.utils.ftp_probe_runner as fpr
    import gui.utils.ftp_probe_cache as fpc
    import gui.utils.probe_patterns as pp

    fake_snapshot = {
        "shares": [{"directories": [{"name": "pub"}, {"name": "incoming"}]}],
    }
    with patch.object(fpr, "run_ftp_probe", return_value=fake_snapshot), \
         patch.object(fpc, "get_ftp_cache_path", return_value=Path("/tmp/fake_ftp_probe.json")), \
         patch.object(pp, "attach_indicator_analysis", return_value={"is_suspicious": False, "matches": []}):
        stub.db_reader.upsert_probe_cache_for_host = MagicMock()
        result = stub._execute_probe_target("job-1", target, {}, threading.Event())

    assert result["status"] == "success"
    assert result["units"] == 1
    assert "2 directorie(s)" in result["notes"]
    stub.db_reader.upsert_probe_cache_for_host.assert_called_once()


def test_probe_ftp_root_files_only_persists_loose_files_marker():
    stub = _BatchMixinStub()
    target = {
        "ip_address": "1.2.3.4",
        "host_type": "F",
        "row_key": "F:8",
        "shares": [],
        "data": {"port": 21},
    }

    import gui.utils.ftp_probe_runner as fpr
    import gui.utils.ftp_probe_cache as fpc
    import gui.utils.probe_patterns as pp

    fake_snapshot = {
        "shares": [{"directories": [], "root_files": ["dump.sql"]}],
    }
    with patch.object(fpr, "run_ftp_probe", return_value=fake_snapshot), \
         patch.object(fpc, "get_ftp_cache_path", return_value=Path("/tmp/fake_ftp_probe.json")), \
         patch.object(pp, "attach_indicator_analysis", return_value={"is_suspicious": False, "matches": []}):
        stub.db_reader.upsert_probe_cache_for_host = MagicMock()
        result = stub._execute_probe_target("job-1", target, {}, threading.Event())

    assert result["status"] == "success"
    call_kwargs = stub.db_reader.upsert_probe_cache_for_host.call_args[1]
    assert call_kwargs["accessible_dirs_count"] == 1
    assert call_kwargs["accessible_dirs_list"] == "[[loose files]]"


def test_probe_http_row_uses_probe_hints_from_http_detail(monkeypatch):
    stub = _BatchMixinStub()
    target = {
        "ip_address": "67.205.33.18",
        "host_type": "H",
        "row_key": "H:11",
        "shares": [],
        "data": {},
    }
    stub.all_servers = [
        {
            "row_key": "H:11",
            "host_type": "H",
            "ip_address": "67.205.33.18",
        }
    ]
    stub.db_reader.get_http_server_detail.return_value = {
        "port": 443,
        "scheme": "https",
        "probe_host": "www.bound2burst.net",
        "probe_path": "/movies/",
    }
    stub.db_reader.upsert_probe_cache_for_host = MagicMock()

    dispatch_calls = []

    monkeypatch.setitem(
        stub._execute_probe_target.__globals__,
        "dispatch_probe_run",
        lambda *a, **kw: (
            dispatch_calls.append((a, kw)) or {"shares": [{"directories": [], "root_files": []}]}
        ),
    )

    import gui.utils.probe_patterns as pp
    monkeypatch.setattr(
        pp,
        "attach_indicator_analysis",
        lambda _r, _p: {"is_suspicious": False, "matches": []},
    )
    monkeypatch.setitem(
        stub._execute_probe_target.__globals__,
        "summarize_probe_snapshot",
        lambda _snap: {"directory_names": [], "display_entries": [], "total_file_count": 0},
    )

    result = stub._execute_probe_target("job-1", target, {"limits": {}}, threading.Event())

    assert result["status"] == "success"
    assert dispatch_calls
    kwargs = dispatch_calls[0][1]
    assert kwargs["port"] == 443
    assert kwargs["scheme"] == "https"
    assert kwargs["request_host"] == "www.bound2burst.net"
    assert kwargs["start_path"] == "/movies/"


def test_probe_smb_row_returns_units_1(monkeypatch):
    """_execute_probe_target with host_type='S' returns units=1 regardless of share count."""
    stub = _BatchMixinStub()
    target = {
        "ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:42",
        "shares": ["docs", "data", "backup"],
        "auth_method": "anonymous",
    }

    fake_result = {"shares": [{"share_name": "docs"}, {"share_name": "data"}, {"share_name": "backup"}]}

    import gui.utils.probe_runner as pr
    import gui.utils.probe_cache as pc
    import gui.utils.probe_patterns as pp

    monkeypatch.setattr(pr, "run_probe", lambda *a, **kw: fake_result)
    monkeypatch.setattr(pc, "save_probe_result", lambda ip, r: None)
    monkeypatch.setattr(pc, "get_probe_result_path", lambda ip: None, raising=False)
    monkeypatch.setattr(pp, "attach_indicator_analysis", lambda r, p: {"is_suspicious": False, "matches": []})
    stub.db_reader.upsert_probe_cache_for_host = MagicMock()

    result = stub._execute_probe_target("job-1", target, {"limits": {}}, threading.Event())

    assert result["status"] == "success"
    assert result["units"] == 1, "units must be 1 regardless of share count"
    assert "3 share(s)" in result["notes"]


def test_probe_smb_rce_is_forced_off_when_session_locked(monkeypatch):
    """_execute_probe_target ignores enable_rce when session RCE gate is locked."""
    stub = _BatchMixinStub()
    stub._rce_unlocked = False
    target = {
        "ip_address": "1.2.3.4",
        "host_type": "S",
        "row_key": "S:42",
        "shares": ["docs"],
        "auth_method": "anonymous",
    }

    dispatch_calls = []

    monkeypatch.setitem(
        stub._execute_probe_target.__globals__,
        "dispatch_probe_run",
        lambda *args, **kwargs: dispatch_calls.append((args, kwargs)) or {
            "shares": [{"share_name": "docs"}],
            "rce_analysis": {"rce_status": "flagged"},
        },
    )
    monkeypatch.setitem(
        stub._execute_probe_target.__globals__,
        "probe_patterns",
        types.SimpleNamespace(attach_indicator_analysis=lambda _r, _p: {"is_suspicious": False, "matches": []}),
    )
    stub.db_reader.upsert_probe_snapshot_for_host = MagicMock(return_value=1)
    stub.db_reader.upsert_probe_cache_for_host = MagicMock()

    result = stub._execute_probe_target(
        "job-1",
        target,
        {"limits": {}, "enable_rce": True},
        threading.Event(),
    )

    assert result["status"] == "success"
    assert "RCE:" not in result["notes"]
    assert dispatch_calls, "dispatch_probe_run should be called"
    assert dispatch_calls[0][1]["enable_rce"] is False


# ---------------------------------------------------------------------------
# _execute_extract_target
# ---------------------------------------------------------------------------


def test_extract_ftp_row_routes_protocol_runner(monkeypatch, tmp_path):
    stub = _BatchMixinStub()
    target = {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:7", "shares": [], "port": 2121}
    options = {
        "download_path": str(tmp_path),
        "clamav_config": {},
        "http_allow_insecure_tls": True,
        "max_total_size_mb": 10,
        "max_file_size_mb": 5,
        "max_files_per_target": 3,
        "max_time_seconds": 10,
        "max_directory_depth": 2,
        "included_extensions": [],
        "excluded_extensions": [],
        "download_delay_seconds": 0,
        "connection_timeout": 5,
        "extension_mode": "download_all",
    }
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.create_quarantine_dir",
        lambda ip, purpose, base_path: tmp_path / ip / "20260421",
    )
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.extract_runner.write_extract_log",
        lambda summary: tmp_path / "extract_log.json",
    )

    captured = {}

    def _fake_run_ftp_extract(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "totals": {"files_downloaded": 1, "bytes_downloaded": 512},
            "timed_out": False,
            "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.protocol_extract_runner.run_ftp_extract",
        _fake_run_ftp_extract,
    )

    result = stub._execute_extract_target("job-1", target, options, threading.Event())
    assert result["status"] == "success"
    assert captured["args"][0] == "1.2.3.4"
    assert captured["kwargs"]["port"] == 2121


def test_extract_http_row_routes_protocol_runner(monkeypatch, tmp_path):
    stub = _BatchMixinStub()
    stub.db_reader.get_http_server_detail.return_value = {
        "port": 443,
        "scheme": "https",
        "probe_host": "files.example.org",
        "probe_path": "/dump/",
    }
    target = {
        "ip_address": "5.6.7.8",
        "host_type": "H",
        "row_key": "H:9",
        "protocol_server_id": 9,
        "data": {},
    }
    options = {
        "download_path": str(tmp_path),
        "clamav_config": {},
        "http_allow_insecure_tls": False,
        "max_total_size_mb": 10,
        "max_file_size_mb": 5,
        "max_files_per_target": 3,
        "max_time_seconds": 10,
        "max_directory_depth": 2,
        "included_extensions": [],
        "excluded_extensions": [],
        "download_delay_seconds": 0,
        "connection_timeout": 5,
        "extension_mode": "download_all",
    }
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.create_quarantine_dir",
        lambda ip, purpose, base_path: tmp_path / ip / "20260421",
    )
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.extract_runner.write_extract_log",
        lambda summary: tmp_path / "extract_log.json",
    )

    captured = {}

    def _fake_run_http_extract(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "totals": {"files_downloaded": 2, "bytes_downloaded": 2048},
            "timed_out": False,
            "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.protocol_extract_runner.run_http_extract",
        _fake_run_http_extract,
    )

    result = stub._execute_extract_target("job-1", target, options, threading.Event())
    assert result["status"] == "success"
    assert captured["args"][0] == "5.6.7.8"
    assert captured["kwargs"]["port"] == 443
    assert captured["kwargs"]["scheme"] == "https"
    assert captured["kwargs"]["request_host"] == "files.example.org"
    assert captured["kwargs"]["start_path"] == "/dump/"
    assert captured["kwargs"]["allow_insecure_tls"] is False


# ---------------------------------------------------------------------------
# _launch_browse_workflow
# ---------------------------------------------------------------------------


def test_browse_ftp_row_opens_ftp_browser():
    """_launch_browse_workflow with host_type='F' instantiates FtpBrowserWindow."""
    stub = _BatchMixinStub()
    target = {
        "ip_address": "10.0.0.1",
        "host_type": "F",
        "row_key": "F:3",
        "auth_method": "",
        "data": {"port": 2121, "banner": "vsftpd 3.0"},
    }

    ftp_instances = []

    class FakeFtpBrowserWindow:
        def __init__(self, **kwargs):
            ftp_instances.append(kwargs)

    with patch("gui.components.unified_browser_window.FtpBrowserWindow", FakeFtpBrowserWindow):
        stub._launch_browse_workflow(target)

    # The import inside the function may use a different path — we use a side-channel check:
    # either the patch worked, or we verify no FileBrowserWindow was opened.
    # The definitive assertion: no FileBrowserWindow call, and an FtpBrowserWindow was created.
    assert len(ftp_instances) == 1
    assert ftp_instances[0]["ip_address"] == "10.0.0.1"
    assert ftp_instances[0]["port"] == 2121
    assert ftp_instances[0]["banner"] == "vsftpd 3.0"


def test_browse_http_row_opens_http_browser():
    """_launch_browse_workflow with host_type='H' instantiates HttpBrowserWindow."""
    stub = _BatchMixinStub()
    stub.db_reader.get_http_server_detail.return_value = {"port": 8080, "scheme": "http"}
    target = {
        "ip_address": "10.0.0.2",
        "host_type": "H",
        "row_key": "H:4",
        "auth_method": "",
        "data": {"banner": "nginx/1.24"},
    }

    http_instances = []

    class FakeHttpBrowserWindow:
        def __init__(self, **kwargs):
            http_instances.append(kwargs)

    with patch("gui.components.unified_browser_window.HttpBrowserWindow", FakeHttpBrowserWindow):
        stub._launch_browse_workflow(target)

    assert len(http_instances) == 1
    assert http_instances[0]["ip_address"] == "10.0.0.2"
    assert http_instances[0]["port"] == 8080
    assert http_instances[0]["scheme"] == "http"
    assert http_instances[0]["banner"] == "nginx/1.24"


def test_ftp_server_picker_browse_routes_via_factory(monkeypatch):
    """_on_open_browser routes through open_ftp_http_browser, not FtpBrowserWindow directly."""
    from gui.components.ftp_server_picker import FtpServerPickerDialog

    picker = object.__new__(FtpServerPickerDialog)

    class _FakeTree:
        def selection(self):
            return ["item1"]

        def item(self, iid, key):
            return ["10.0.0.5", "2121"]

    picker.tree = _FakeTree()
    picker._rows_by_item_id = {"item1": {"banner": "ProFTPD 1.3"}}
    picker._dialog = None
    picker._config_path = None
    picker._db_reader = None
    picker._theme = None
    picker._settings_manager = None

    calls = []
    import gui.components.unified_browser_window as ubw
    monkeypatch.setattr(ubw, "open_ftp_http_browser", lambda *a, **kw: calls.append((a, kw)))
    picker._on_open_browser()

    assert len(calls) == 1
    args, kw = calls[0]
    assert args[0] == "F"                  # routing intent
    assert kw["ip_address"] == "10.0.0.5"
    assert kw["port"] == 2121
    assert kw["banner"] == "ProFTPD 1.3"


def test_browse_smb_row_routes_via_open_smb_browser(monkeypatch):
    """_launch_browse_workflow with host_type='S' calls open_smb_browser.

    Verifies ip_address, shares, auth_method, and on_extracted are forwarded.
    Primary runtime path: _launch_browse_workflow (not the details fallback).
    """
    stub = _BatchMixinStub()
    stub.db_reader.get_accessible_shares.return_value = [{"share_name": "docs"}]
    stub.db_reader.get_share_credentials.return_value = []
    target = {
        "ip_address": "10.0.0.3",
        "host_type": "S",
        "row_key": "S:5",
        "auth_method": "anonymous",
        "data": {},
    }

    calls = []
    import gui.components.unified_browser_window as ubw
    monkeypatch.setattr(ubw, "open_smb_browser", lambda *a, **kw: calls.append(kw))
    stub._launch_browse_workflow(target)

    assert len(calls) == 1
    assert calls[0]["ip_address"] == "10.0.0.3"
    assert calls[0]["shares"] == ["docs"]
    assert calls[0]["auth_method"] == "anonymous"
    assert calls[0]["on_extracted"] == stub._handle_extracted_update


# ---------------------------------------------------------------------------
# Probe progress per-target invariant
# ---------------------------------------------------------------------------


def test_probe_progress_per_target_invariant(monkeypatch):
    """Mixed S+F probe: total=len(targets), S/F both return units=1."""
    stub = _BatchMixinStub()

    import gui.utils.probe_runner as pr
    import gui.utils.probe_cache as pc
    import gui.utils.probe_patterns as pp
    import gui.utils.ftp_probe_runner as fpr
    import gui.utils.ftp_probe_cache as fpc

    monkeypatch.setattr(pr, "run_probe", lambda *a, **kw: {"shares": [{"share_name": "docs"}, {"share_name": "data"}]})
    monkeypatch.setattr(pc, "save_probe_result", lambda ip, r: None)
    monkeypatch.setattr(pc, "get_probe_result_path", lambda ip: None, raising=False)
    monkeypatch.setattr(pp, "attach_indicator_analysis", lambda r, p: {"is_suspicious": False, "matches": []})
    monkeypatch.setattr(fpr, "run_ftp_probe", lambda *a, **kw: {"shares": [{"directories": [{"name": "pub"}]}]})
    monkeypatch.setattr(fpc, "get_ftp_cache_path", lambda ip: Path("/tmp/fake_ftp_probe.json"))
    stub.db_reader.upsert_probe_cache_for_host = MagicMock()

    targets = [
        {"ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:42",
         "shares": ["docs", "data"], "auth_method": "anonymous"},
        {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:7", "shares": []},
    ]

    total_units = len(targets)  # Option A: 2
    results = [
        stub._execute_probe_target("j", t, {"limits": {}}, threading.Event())
        for t in targets
    ]

    completed = sum(r.get("units", 1) for r in results)
    assert completed == total_units, "completed must equal total_units"
    assert results[0]["units"] == 1
    assert results[1]["units"] == 1
    assert results[1]["status"] == "success"


# ---------------------------------------------------------------------------
# _attach_probe_status: FTP row uses DB value, not SMB cache
# ---------------------------------------------------------------------------


def test_attach_probe_status_ftp_row_does_not_use_smb_cache():
    """F row: _attach_probe_status uses DB-supplied probe_status, never _determine_probe_status."""
    stub = _BatchMixinStub()
    servers = [
        {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:2",
         "probe_status": "clean",  # DB-supplied
         "rce_status": None, "extracted": 0},
    ]

    calls_to_determine = []
    original_determine = stub._determine_probe_status
    stub._determine_probe_status = lambda ip: (calls_to_determine.append(ip) or "unprobed")

    stub._attach_probe_status(servers)

    assert calls_to_determine == [], "_determine_probe_status must NOT be called for F rows"
    assert servers[0]["probe_status"] == "clean"


# ---------------------------------------------------------------------------
# _on_pry_selected: FTP guard
# ---------------------------------------------------------------------------


def test_pry_blocked_for_ftp_row():
    """_on_pry_selected with F row target shows warning and returns without launching pry."""
    stub = _BatchMixinStub()
    stub.filtered_servers = [
        {"ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:1",
         "auth_method": "", "accessible_shares_list": ""}
    ]
    stub.tree._items = {"F:1": True}
    stub.tree._selection = ["F:1"]

    warning_shown = []
    pry_started = []

    with patch("tkinter.messagebox.showwarning", side_effect=lambda t, m, **kw: warning_shown.append(t)):
        with patch.object(stub, "_start_batch_job", side_effect=lambda *a, **kw: pry_started.append(a)):
            stub._on_pry_selected()

    assert len(warning_shown) == 1, "Warning dialog must be shown"
    assert "Pry" in warning_shown[0]
    assert pry_started == [], "Pry must not be launched for FTP rows"


def test_pry_blocked_when_session_locked():
    """_on_pry_selected returns early when pry is locked for the session."""
    stub = _BatchMixinStub()
    stub._pry_unlocked = False
    stub.filtered_servers = [
        {"ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:1", "auth_method": "anonymous"}
    ]
    stub.tree._items = {"S:1": True}
    stub.tree._selection = ["S:1"]

    warnings = []
    pry_started = []
    with patch(
        "gui.components.server_list_window.actions.batch_operations.messagebox.showwarning",
        side_effect=lambda title, _msg, **_kw: warnings.append(title),
    ):
        with patch.object(stub, "_start_batch_job", side_effect=lambda *a, **kw: pry_started.append((a, kw))):
            stub._on_pry_selected()

    assert warnings == ["Pry Disabled"]
    assert pry_started == []


def test_start_batch_job_pry_blocked_when_session_locked():
    """_start_batch_job hard-gates pry when unlock flag is not active."""
    stub = _BatchMixinStub()
    stub._pry_unlocked = False

    warnings = []
    with patch(
        "gui.components.server_list_window.actions.batch.messagebox.showwarning",
        side_effect=lambda title, _msg, **_kw: warnings.append(title),
    ):
        stub._start_batch_job(
            "pry",
            [{"ip_address": "1.2.3.4", "host_type": "S", "row_key": "S:1"}],
            {},
        )

    assert warnings == ["Pry Disabled"]
    assert stub.active_jobs == {}


# ---------------------------------------------------------------------------
# Context-menu mark toggles
# ---------------------------------------------------------------------------


def test_mark_favorite_toggles_selected_row_only():
    """Context-menu Mark Favorite toggles one selected row via row_key."""
    stub = _BatchMixinStub()
    row = {
        "ip_address": "1.2.3.4",
        "host_type": "S",
        "row_key": "S:1",
        "favorite": 0,
        "avoid": 0,
        "probe_status": "unprobed",
        "indicator_matches": 0,
    }
    stub.filtered_servers = [row]
    stub.all_servers = [row]
    stub.tree._items = {"S:1": True}
    stub.tree._selection = ["S:1"]

    toggle_calls = []

    def _fake_apply_flag_toggle(row_key, field, new_value):
        toggle_calls.append((row_key, field, new_value))
        row[field] = new_value

    stub._apply_flag_toggle = _fake_apply_flag_toggle
    stub._apply_filters = MagicMock()

    stub._on_mark_favorite_selected()

    assert toggle_calls == [("S:1", "favorite", 1)]
    assert row["favorite"] == 1
    stub._apply_filters.assert_called_once()


def test_mark_avoid_bulk_toggles_per_row_current_state():
    """Bulk Mark Avoid flips each selected row independently."""
    stub = _BatchMixinStub()
    row_a = {"ip_address": "1.1.1.1", "host_type": "S", "row_key": "S:1", "favorite": 0, "avoid": 0}
    row_b = {"ip_address": "2.2.2.2", "host_type": "H", "row_key": "H:2", "favorite": 0, "avoid": 1}
    stub.filtered_servers = [row_a, row_b]
    stub.all_servers = [row_a, row_b]
    stub.tree._items = {"S:1": True, "H:2": True}
    stub.tree._selection = ["S:1", "H:2"]

    toggle_calls = []

    def _fake_apply_flag_toggle(row_key, field, new_value):
        toggle_calls.append((row_key, field, new_value))
        row = row_a if row_key == "S:1" else row_b
        row[field] = new_value

    stub._apply_flag_toggle = _fake_apply_flag_toggle
    stub._apply_filters = MagicMock()

    stub._on_mark_avoid_selected()

    assert set(toggle_calls) == {
        ("S:1", "avoid", 1),  # 0 -> 1
        ("H:2", "avoid", 0),  # 1 -> 0
    }
    assert row_a["avoid"] == 1
    assert row_b["avoid"] == 0
    stub._apply_filters.assert_called_once()


def test_mark_compromised_toggles_selected_protocol_row_only():
    """Same IP S+H rows: toggling compromised on H row must not mutate S row."""
    stub = _BatchMixinStub()
    smb_row = {
        "ip_address": "9.9.9.9",
        "host_type": "S",
        "row_key": "S:10",
        "probe_status": "clean",
        "probe_status_emoji": "✔",
        "indicator_matches": 0,
    }
    http_row = {
        "ip_address": "9.9.9.9",
        "host_type": "H",
        "row_key": "H:10",
        "probe_status": "clean",
        "probe_status_emoji": "✔",
        "indicator_matches": 0,
    }
    stub.filtered_servers = [smb_row, http_row]
    stub.all_servers = [smb_row, http_row]
    stub.tree._items = {"S:10": True, "H:10": True}
    stub.tree._selection = ["H:10"]
    stub._apply_filters = MagicMock()
    stub.db_reader.upsert_probe_cache_for_host = MagicMock()

    stub._on_mark_compromised_selected()

    assert http_row["probe_status"] == "issue"
    assert http_row["indicator_matches"] == 1
    assert smb_row["probe_status"] == "clean", "SMB sibling must remain unchanged"
    assert smb_row["indicator_matches"] == 0

    assert stub.db_reader.upsert_probe_cache_for_host.call_count == 1
    _, kwargs = stub.db_reader.upsert_probe_cache_for_host.call_args
    assert kwargs["status"] == "issue"
    assert kwargs["indicator_matches"] == 1
    stub._apply_filters.assert_called_once()

    # Toggle again -> unmark compromised
    stub._apply_filters.reset_mock()
    stub.tree._selection = ["H:10"]
    stub._on_mark_compromised_selected()

    assert http_row["probe_status"] == "clean"
    assert http_row["indicator_matches"] == 0
    _, kwargs2 = stub.db_reader.upsert_probe_cache_for_host.call_args
    assert kwargs2["status"] == "clean"
    assert kwargs2["indicator_matches"] == 0
    stub._apply_filters.assert_called_once()
