"""Tests for shared sidecar promotion payload handling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.sidecar_promotion import (
    SidecarPromotionError,
    build_probe_cache_payload,
    build_manual_record_payload,
    build_probe_snapshot_payload,
    format_promotion_success,
    promote_sidecar_prefill,
)


def test_build_http_payload_resolves_domain_and_preserves_probe_hints():
    def _resolver(*_args, **_kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    payload = build_manual_record_payload(
        {
            "host_type": "H",
            "host": "example.com",
            "scheme": "https",
            "_probe_host_hint": "example.com",
            "_probe_path_hint": "files/?q=1",
        },
        resolver=_resolver,
    )

    assert payload == {
        "host_type": "H",
        "ip_address": "93.184.216.34",
        "port": 443,
        "scheme": "https",
        "probe_host": "example.com",
        "probe_path": "/files/",
    }


def test_build_http_payload_defaults_port_from_scheme():
    payload = build_manual_record_payload({
        "host_type": "H",
        "host": "1.2.3.4",
        "scheme": "http",
    })
    assert payload["port"] == 80
    assert payload["scheme"] == "http"


def test_build_ftp_payload_defaults_port():
    payload = build_manual_record_payload({
        "host_type": "F",
        "host": "1.2.3.4",
    })
    assert payload == {
        "host_type": "F",
        "ip_address": "1.2.3.4",
        "port": 21,
    }


def test_build_smb_payload_supported():
    payload = build_manual_record_payload({
        "host_type": "S",
        "host": "10.20.30.40",
    })
    assert payload == {
        "host_type": "S",
        "ip_address": "10.20.30.40",
    }


def test_unresolved_domain_is_rejected():
    def _resolver(*_args, **_kwargs):
        raise OSError("no dns")

    with pytest.raises(SidecarPromotionError, match="Could not resolve"):
        build_manual_record_payload(
            {"host_type": "H", "host": "missing.example.invalid"},
            resolver=_resolver,
        )


def test_unknown_host_type_is_rejected():
    with pytest.raises(SidecarPromotionError, match="Only SMB, FTP, and HTTP"):
        build_manual_record_payload({"host_type": "X", "host": "1.2.3.4"})


def test_promote_sidecar_prefill_writes_and_clears_cache():
    reader = MagicMock()
    reader.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 4,
        "row_key": "H:4",
        "operation": "insert",
    }

    result = promote_sidecar_prefill(
        reader,
        {"host_type": "H", "host": "1.2.3.4", "port": 8080, "scheme": "http"},
    )

    reader.upsert_manual_server_record.assert_called_once_with({
        "host_type": "H",
        "ip_address": "1.2.3.4",
        "port": 8080,
        "scheme": "http",
    })
    reader.clear_cache.assert_called_once()
    assert result["result"]["row_key"] == "H:4"
    assert result["probe_cache_copied"] is False


def test_promote_sidecar_prefill_copies_cacheable_probe_artifact():
    reader = MagicMock()
    reader.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 4,
        "row_key": "H:4",
        "operation": "insert",
    }

    result = promote_sidecar_prefill(
        reader,
        {
            "host_type": "H",
            "host": "1.2.3.4",
            "port": 8080,
            "scheme": "http",
            "_probe_cache": {
                "status": "issue",
                "indicator_matches": 2,
                "preview": "pub, movies ,",
                "checked_at": "2026-05-03T10:20:30",
            },
        },
    )

    reader.upsert_probe_cache_for_host.assert_called_once_with(
        "1.2.3.4",
        "H",
        protocol_server_id=4,
        port=8080,
        status="issue",
        indicator_matches=2,
        snapshot_path=None,
        last_probe_at="2026-05-03T10:20:30",
        accessible_dirs_list="pub,movies",
        accessible_dirs_count=2,
        accessible_files_count=None,
    )
    assert result["probe_cache_copied"] is True


def test_promote_sidecar_prefill_copies_full_probe_snapshot_and_links_cache():
    reader = MagicMock()
    reader.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 4,
        "row_key": "H:4",
        "operation": "insert",
    }
    reader.upsert_probe_snapshot_for_host.return_value = 99
    snapshot = {
        "run_at": "2026-05-03T10:20:30",
        "shares": [
            {
                "share": "http_root",
                "root_files": ["index.html"],
                "directories": [{"name": "pub", "files": ["readme.txt"]}],
            }
        ],
    }

    result = promote_sidecar_prefill(
        reader,
        {
            "host_type": "H",
            "host": "1.2.3.4",
            "port": 8080,
            "scheme": "http",
            "_probe_snapshot": snapshot,
            "_probe_snapshot_source": "sidecar:se_dork",
            "_probe_cache": {
                "status": "clean",
                "indicator_matches": 0,
                "preview": "legacy-preview",
                "checked_at": "2026-05-03T10:20:30",
            },
        },
    )

    reader.upsert_probe_snapshot_for_host.assert_called_once_with(
        "1.2.3.4",
        "H",
        snapshot,
        protocol_server_id=4,
        port=8080,
        source="sidecar:se_dork",
    )
    reader.upsert_probe_cache_for_host.assert_called_once_with(
        "1.2.3.4",
        "H",
        protocol_server_id=4,
        port=8080,
        status="clean",
        indicator_matches=0,
        snapshot_path=None,
        last_probe_at="2026-05-03T10:20:30",
        accessible_dirs_list="pub,[[loose files]]",
        accessible_dirs_count=1,
        accessible_files_count=2,
        latest_snapshot_id=99,
    )
    assert result["probe_snapshot_copied"] is True
    assert result["probe_snapshot_id"] == 99


def test_build_probe_snapshot_payload_ignores_invalid_json():
    assert build_probe_snapshot_payload({"_probe_snapshot": "{not json"}) is None


def test_unprobed_artifact_does_not_build_probe_cache_payload():
    assert build_probe_cache_payload({
        "_probe_cache": {
            "status": "unprobed",
            "indicator_matches": 0,
            "preview": "pub",
        }
    }) is None


def test_unprobed_artifact_does_not_write_snapshot():
    reader = MagicMock()
    reader.upsert_manual_server_record.return_value = {
        "host_type": "H",
        "protocol_server_id": 4,
        "row_key": "H:4",
        "operation": "insert",
    }

    result = promote_sidecar_prefill(
        reader,
        {
            "host_type": "H",
            "host": "1.2.3.4",
            "port": 8080,
            "scheme": "http",
            "_probe_snapshot": {"shares": []},
            "_probe_cache": {
                "status": "unprobed",
                "indicator_matches": 0,
            },
        },
    )

    reader.upsert_probe_snapshot_for_host.assert_not_called()
    reader.upsert_probe_cache_for_host.assert_not_called()
    assert result["probe_snapshot_copied"] is False


def test_format_promotion_success_mentions_slb_filters():
    message = format_promotion_success({
        "payload": {"host_type": "H", "ip_address": "1.2.3.4", "port": 8080},
        "result": {"host_type": "H", "operation": "update"},
    })

    assert "HTTP record update: 1.2.3.4:8080" in message
    assert "Server List Browser" in message


def test_format_promotion_success_mentions_probe_copy_when_present():
    message = format_promotion_success({
        "payload": {"host_type": "H", "ip_address": "1.2.3.4", "port": 8080},
        "result": {"host_type": "H", "operation": "insert"},
        "probe_cache_copied": True,
    })

    assert "Probe summary copied from sidecar" in message


def test_format_promotion_success_prefers_snapshot_copy_message():
    message = format_promotion_success({
        "payload": {"host_type": "H", "ip_address": "1.2.3.4", "port": 8080},
        "result": {"host_type": "H", "operation": "insert"},
        "probe_cache_copied": True,
        "probe_snapshot_copied": True,
    })

    assert "Probe snapshot copied from sidecar" in message
