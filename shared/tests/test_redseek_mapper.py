"""Unit tests for experimental/redseek/mapper.py."""

from __future__ import annotations

import json

from experimental.redseek.mapper import row_to_prefill

_SRC = "reddit_run_sync"
_SNAP_SRC = "reddit:run_sync"

_BROWSER_SRC = "reddit_browser"
_BROWSER_SNAP_SRC = "sidecar:reddit"


def _row(**kwargs) -> dict:
    base = dict(
        protocol="http",
        host="1.2.3.4",
        target_normalized="http://1.2.3.4/files/",
        probe_status="unprobed",
        probe_indicator_matches=0,
        probe_preview=None,
        probe_checked_at=None,
        probe_error=None,
        probe_snapshot_json=None,
    )
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Protocol mapping
# ---------------------------------------------------------------------------

def test_smb_maps_to_host_type_s():
    r = row_to_prefill(_row(protocol="smb", target_normalized="smb://1.2.3.4/share"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["host_type"] == "S"
    assert r["host"] == "1.2.3.4"
    assert r["scheme"] is None


def test_smb_default_port_is_none_when_not_explicit():
    r = row_to_prefill(_row(protocol="smb", target_normalized="smb://1.2.3.4/share"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["port"] is None


def test_smb_explicit_port_preserved():
    r = row_to_prefill(_row(protocol="smb", target_normalized="smb://1.2.3.4:4445/share"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["port"] == 4445


def test_ftp_maps_to_host_type_f():
    r = row_to_prefill(_row(protocol="ftp", target_normalized="ftp://1.2.3.4/pub/"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["host_type"] == "F"
    assert r["host"] == "1.2.3.4"
    assert r["scheme"] is None


def test_ftp_explicit_port_preserved():
    r = row_to_prefill(_row(protocol="ftp", target_normalized="ftp://1.2.3.4:2121/pub/"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["port"] == 2121


def test_http_with_path_and_port():
    r = row_to_prefill(
        _row(protocol="http", target_normalized="http://1.2.3.4:8080/files/data/"),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["host_type"] == "H"
    assert r["port"] == 8080
    assert r["_probe_path_hint"] == "/files/data/"


def test_https_maps_scheme_and_port():
    r = row_to_prefill(
        _row(protocol="https", target_normalized="https://1.2.3.4/secure/"),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["host_type"] == "H"
    assert r["scheme"] == "https"
    assert r["port"] is None


def test_unknown_protocol_returns_none():
    r = row_to_prefill(_row(protocol="gopher"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is None


def test_empty_protocol_returns_none():
    r = row_to_prefill(_row(protocol=""), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is None


def test_hostless_row_returns_none():
    r = row_to_prefill(_row(protocol="http", host=""), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is None


# ---------------------------------------------------------------------------
# Probe metadata
# ---------------------------------------------------------------------------

def test_probe_cache_fields_carried():
    r = row_to_prefill(
        _row(
            probe_status="clean",
            probe_indicator_matches=2,
            probe_preview="dir listing",
            probe_checked_at="2026-01-01T00:00:00",
            probe_error=None,
        ),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    cache = r["_probe_cache"]
    assert cache["status"] == "clean"
    assert cache["indicator_matches"] == 2
    assert cache["preview"] == "dir listing"
    assert cache["checked_at"] == "2026-01-01T00:00:00"
    assert cache["error"] is None


def test_probe_snapshot_json_valid_becomes_probe_snapshot():
    payload = {"files": 3, "dirs": 1}
    r = row_to_prefill(
        _row(probe_snapshot_json=json.dumps(payload)),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["_probe_snapshot"] == payload


def test_probe_snapshot_json_invalid_is_ignored():
    r = row_to_prefill(_row(probe_snapshot_json="not json"), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert "_probe_snapshot" not in r


def test_probe_snapshot_json_non_dict_is_ignored():
    r = row_to_prefill(_row(probe_snapshot_json=json.dumps([1, 2])), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert "_probe_snapshot" not in r


def test_probe_snapshot_json_null_is_ignored():
    r = row_to_prefill(_row(probe_snapshot_json=None), promotion_source=_SRC, snapshot_source=_SNAP_SRC)
    assert r is not None
    assert "_probe_snapshot" not in r


# ---------------------------------------------------------------------------
# Source labels
# ---------------------------------------------------------------------------

def test_promotion_source_label_is_set():
    r = row_to_prefill(_row(), promotion_source="reddit_run_sync", snapshot_source=_SNAP_SRC)
    assert r is not None
    assert r["_promotion_source"] == "reddit_run_sync"


def test_snapshot_source_label_is_set():
    r = row_to_prefill(_row(), promotion_source=_SRC, snapshot_source="reddit:run_sync")
    assert r is not None
    assert r["_probe_snapshot_source"] == "reddit:run_sync"


def test_browser_source_labels():
    r = row_to_prefill(_row(), promotion_source=_BROWSER_SRC, snapshot_source=_BROWSER_SNAP_SRC)
    assert r is not None
    assert r["_promotion_source"] == "reddit_browser"
    assert r["_probe_snapshot_source"] == "sidecar:reddit"


# ---------------------------------------------------------------------------
# HTTP path hint normalization
# ---------------------------------------------------------------------------

def test_http_path_hints_strip_query_and_fragment():
    r = row_to_prefill(
        _row(protocol="http", target_normalized="http://1.2.3.4/files/data/?page=2#anchor"),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["_probe_path_hint"] == "/files/data/"


def test_http_path_hints_normalize_with_snapshot():
    payload = {"listing": True}
    r = row_to_prefill(
        _row(
            protocol="http",
            target_normalized="http://1.2.3.4/archive/?sort=name",
            probe_snapshot_json=json.dumps(payload),
        ),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["_probe_path_hint"] == "/archive/"
    assert r["_probe_snapshot"] == payload


def test_http_root_path_defaults_to_slash():
    r = row_to_prefill(
        _row(protocol="http", target_normalized="http://1.2.3.4"),
        promotion_source=_SRC,
        snapshot_source=_SNAP_SRC,
    )
    assert r is not None
    assert r["_probe_path_hint"] == "/"
