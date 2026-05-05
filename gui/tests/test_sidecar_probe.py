"""Tests for shared sidecar probe target mapping and outcome handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.utils.sidecar_probe import (
    PROBE_STATUS_CLEAN,
    PROBE_STATUS_UNPROBED,
    SidecarProbeUnsupported,
    build_probe_target_from_sidecar_row,
    run_sidecar_probe,
)


def test_build_http_probe_target_defaults_port_and_preserves_path():
    target = build_probe_target_from_sidecar_row({
        "protocol": "https",
        "target_normalized": "https://example.com/files/?q=1",
        "host": "example.com",
    })

    assert target.host_type == "H"
    assert target.host == "example.com"
    assert target.port == 443
    assert target.scheme == "https"
    assert target.request_host == "example.com"
    assert target.start_path == "/files/"


def test_build_ftp_probe_target_defaults_port():
    target = build_probe_target_from_sidecar_row({
        "protocol": "ftp",
        "target_normalized": "ftp://ftp.example.com/pub",
        "host": "ftp.example.com",
    })

    assert target.host_type == "F"
    assert target.host == "ftp.example.com"
    assert target.port == 21
    assert target.scheme is None
    assert target.start_path == "/pub"


def test_unknown_protocol_is_skipped_with_reason():
    with pytest.raises(SidecarProbeUnsupported, match="protocol info is unavailable"):
        build_probe_target_from_sidecar_row({
            "protocol": "unknown",
            "target_normalized": "example.com",
            "host": "example.com",
        })


def test_smb_protocol_is_skipped_without_guessing():
    with pytest.raises(SidecarProbeUnsupported, match="share context"):
        build_probe_target_from_sidecar_row({
            "protocol": "smb",
            "target_normalized": "smb://example.com/share",
            "host": "example.com",
        })


def test_run_sidecar_probe_captures_snapshot_preview(monkeypatch):
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
    calls = []

    def fake_dispatch(host, host_type, **kwargs):
        calls.append((host, host_type, kwargs))
        return snapshot

    monkeypatch.setattr("gui.utils.sidecar_probe.dispatch_probe_run", fake_dispatch)
    monkeypatch.setattr(
        "gui.utils.sidecar_probe.build_indicator_patterns",
        lambda _config_path: [],
    )

    target = build_probe_target_from_sidecar_row({
        "protocol": "http",
        "target_normalized": "http://example.com:8080/files",
        "host": "example.com",
    })
    outcome = run_sidecar_probe(target)

    assert outcome.probe_status == PROBE_STATUS_CLEAN
    assert outcome.probe_preview == "pub,[[loose files]]"
    assert outcome.probe_snapshot_payload is snapshot
    assert calls[0][0] == "example.com"
    assert calls[0][1] == "H"
    assert calls[0][2]["port"] == 8080
    assert calls[0][2]["start_path"] == "/files"


def test_run_sidecar_probe_error_without_shares_is_unprobed(monkeypatch):
    monkeypatch.setattr(
        "gui.utils.sidecar_probe.dispatch_probe_run",
        lambda *_a, **_k: {"shares": [], "errors": ["connection failed"]},
    )

    target = build_probe_target_from_sidecar_row({
        "protocol": "ftp",
        "target_normalized": "ftp://example.com/pub",
        "host": "example.com",
    })
    outcome = run_sidecar_probe(target)

    assert outcome.probe_status == PROBE_STATUS_UNPROBED
    assert outcome.probe_snapshot_payload is None
    assert "connection failed" in outcome.probe_error
