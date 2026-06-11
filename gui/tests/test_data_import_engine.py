"""
Tests for DataImportEngine timestamp normalization (Card 2.5).

Covers:
- Incoming records with T-format timestamps are stored in canonical form.
- Incoming records with UTC-offset timestamps are converted to UTC.
- current_time written by the engine (created_at / updated_at) contains no T.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import gui.utils.data_import_engine as die
from gui.utils.data_import_engine import DataImportEngine

_CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(tmp_path) -> DataImportEngine:
    db = tmp_path / "import_test.db"
    engine = DataImportEngine(str(db))
    engine._ensure_database_schema("servers")
    return engine


def _fetch_server(engine: DataImportEngine, ip: str) -> dict:
    conn = sqlite3.connect(engine.db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM servers WHERE ip_address = ?", (ip,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_import_normalizes_T_format_last_seen(tmp_path):
    """Incoming last_seen with T-separator is stored in canonical space format."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "1.2.3.4",
            "country": "US",
            "auth_method": "anonymous",
            "last_seen": "2025-01-21T14:20:05",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "1.2.3.4")
    assert row, "Row should have been inserted"
    assert "T" not in (row["last_seen"] or ""), (
        f"last_seen has T after import: {row['last_seen']!r}"
    )
    assert row["last_seen"] == "2025-01-21 14:20:05"


def test_import_normalizes_microsecond_timestamps(tmp_path):
    """Incoming timestamp with microseconds is truncated to seconds."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "2.3.4.5",
            "country": "GB",
            "auth_method": "guest",
            "last_seen": "2025-06-01T08:00:05.123456",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "2.3.4.5")
    assert row
    assert row["last_seen"] == "2025-06-01 08:00:05"


def test_import_normalizes_offset_timestamps(tmp_path):
    """Incoming timestamp with UTC offset is converted to UTC canonical form."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "3.4.5.6",
            "country": "DE",
            "auth_method": "anonymous",
            # -05:00 → add 5h → 14:00 UTC
            "last_seen": "2025-01-21T09:00:00-05:00",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "3.4.5.6")
    assert row
    assert row["last_seen"] == "2025-01-21 14:00:00", (
        f"Expected UTC conversion, got: {row['last_seen']!r}"
    )


def test_import_current_time_no_T(tmp_path):
    """created_at and updated_at written by the engine contain no T."""
    engine = _make_engine(tmp_path)
    records = [
        {
            "ip_address": "4.5.6.7",
            "country": "FR",
            "auth_method": "anonymous",
        }
    ]
    engine._import_to_database(records, "servers", "merge", None)

    row = _fetch_server(engine, "4.5.6.7")
    assert row

    created = row.get("created_at", "") or ""
    updated = row.get("updated_at", "") or ""

    assert "T" not in created, f"created_at has T: {created!r}"
    assert "T" not in updated, f"updated_at has T: {updated!r}"

    if created:
        assert _CANONICAL_RE.match(created), (
            f"created_at not canonical: {created!r}"
        )
    if updated:
        assert _CANONICAL_RE.match(updated), (
            f"updated_at not canonical: {updated!r}"
        )


def test_import_merge_update_no_T(tmp_path):
    """updated_at written during a merge-update also contains no T."""
    engine = _make_engine(tmp_path)
    records = [
        {"ip_address": "5.6.7.8", "country": "JP", "auth_method": "anonymous"},
    ]
    engine._import_to_database(records, "servers", "merge", None)

    # Second import triggers UPDATE path
    records2 = [
        {
            "ip_address": "5.6.7.8",
            "country": "JP",
            "auth_method": "anonymous",
            "last_seen": "2025-03-01T12:00:00",
        }
    ]
    engine._import_to_database(records2, "servers", "merge", None)

    row = _fetch_server(engine, "5.6.7.8")
    assert row
    updated = row.get("updated_at", "") or ""
    assert "T" not in updated, f"updated_at has T after update: {updated!r}"


# ---------------------------------------------------------------------------
# C6 - Bounded ZIP import
# ---------------------------------------------------------------------------

_REG = (stat.S_IFREG | 0o644) << 16
_LNK = (stat.S_IFLNK | 0o777) << 16
_PERM_ONLY = 0o600 << 16          # permission bits only, no file-type bits

_VALID_RECORD = {
    "ip_address": "1.1.1.1",
    "country": "US",
    "auth_method": "anonymous",
}


def _engine(tmp_path) -> DataImportEngine:
    return DataImportEngine(str(tmp_path / "import_zip.db"))


def _zi(name, *, size=0, flag_bits=0, external_attr=_REG):
    info = zipfile.ZipInfo(name)
    info.file_size = size
    info.flag_bits = flag_bits
    info.external_attr = external_attr
    return info


class _StubZip:
    """Minimal stand-in for zipfile.ZipFile exposing infolist()/open()."""

    def __init__(self, infos, streams=None, open_exc=None):
        self._infos = list(infos)
        self._streams = streams or {}
        self._open_exc = open_exc

    def infolist(self):
        return list(self._infos)

    def open(self, info, *args, **kwargs):
        if self._open_exc is not None:
            raise self._open_exc
        return self._streams[info.filename]


def _select(engine, infos, streams=None):
    return engine._select_zip_member(_StubZip(infos, streams))


def _track_mkstemp(monkeypatch):
    """Record every temp path mkstemp hands out so cleanup can be asserted."""
    created = []
    real = tempfile.mkstemp

    def fake(*args, **kwargs):
        fd, path = real(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(die.tempfile, "mkstemp", fake)
    return created


def _fd_count():
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return 0


def _make_zip(path, members, compress=zipfile.ZIP_DEFLATED):
    """members: list of (name, str_or_bytes)."""
    with zipfile.ZipFile(path, "w", compress) as z:
        for name, data in members:
            z.writestr(name, data)


# --- member-count / total-size ------------------------------------------------


def test_select_rejects_33_members(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi(f"f{i}.txt") for i in range(32)] + [_zi("payload.json")]
    with pytest.raises(ValueError, match="too many members"):
        _select(engine, infos)


def test_select_accepts_32_members(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi(f"f{i}.txt") for i in range(31)] + [_zi("payload.json")]
    selected = _select(engine, infos)
    assert selected.filename == "payload.json"


def test_select_total_size_boundary(tmp_path):
    engine = _engine(tmp_path)
    json_size = 100 * 1024 * 1024
    filler = die._ZIP_MAX_TOTAL_BYTES - json_size  # sum == exactly the cap
    at_limit = [
        _zi("servers_export.json", size=json_size),
        _zi("pad.txt", size=filler),
    ]
    assert _select(engine, at_limit).filename == "servers_export.json"

    over = [
        _zi("servers_export.json", size=json_size),
        _zi("pad.txt", size=filler + 1),
    ]
    with pytest.raises(ValueError, match="total size"):
        _select(engine, over)


# --- eligibility --------------------------------------------------------------


def test_select_nested_only_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="No CSV or JSON"):
        _select(engine, [_zi("dir/x.json")])


def test_select_backslash_nested_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="No CSV or JSON"):
        _select(engine, [_zi("dir\\x.json")])


def test_select_directory_only_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="No CSV or JSON"):
        _select(engine, [_zi("sub/")])


def test_select_symlink_member_excluded(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="No CSV or JSON"):
        _select(engine, [_zi("payload.json", external_attr=_LNK)])


@pytest.mark.parametrize("attr", [_REG, _PERM_ONLY, 0])
def test_select_regular_modes_accepted(tmp_path, attr):
    engine = _engine(tmp_path)
    selected = _select(engine, [_zi("payload.json", external_attr=attr)])
    assert selected.filename == "payload.json"


# --- metadata exclusion -------------------------------------------------------


def test_select_metadata_only_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="No CSV or JSON"):
        _select(engine, [_zi("export_metadata.json")])


def test_select_metadata_plus_payload_picks_payload(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi("export_metadata.json"), _zi("servers_export.json")]
    assert _select(engine, infos).filename == "servers_export.json"


def test_select_metadata_substring_name_still_eligible(tmp_path):
    engine = _engine(tmp_path)
    selected = _select(engine, [_zi("vuln_metadata_export.json")])
    assert selected.filename == "vuln_metadata_export.json"


# --- duplicates / selection ---------------------------------------------------


def test_select_duplicate_names_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        _select(engine, [_zi("a.json"), _zi("a.json")])


def test_select_prefers_json_over_csv(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi("servers_export.csv"), _zi("servers_export.json")]
    assert _select(engine, infos).filename == "servers_export.json"


def test_select_csv_fallback_lexical(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi("b.csv"), _zi("a.csv")]
    assert _select(engine, infos).filename == "a.csv"


def test_select_multiple_json_lexical(tmp_path):
    engine = _engine(tmp_path)
    infos = [_zi("b_export.json"), _zi("a_export.json")]
    assert _select(engine, infos).filename == "a_export.json"


# --- encryption / selected size ----------------------------------------------


def test_select_encrypted_rejected(tmp_path):
    engine = _engine(tmp_path)
    with pytest.raises(ValueError, match="encrypted"):
        _select(engine, [_zi("payload.json", flag_bits=0x1)])


def test_select_encrypted_not_bypassed_by_csv(tmp_path):
    engine = _engine(tmp_path)
    # A clean CSV exists, but JSON is selected first and is encrypted -> reject.
    infos = [_zi("a.json", flag_bits=0x1), _zi("a.csv")]
    with pytest.raises(ValueError, match="encrypted"):
        _select(engine, infos)


def test_select_selected_size_boundary(tmp_path):
    engine = _engine(tmp_path)
    at_limit = _zi("payload.json", size=die._ZIP_MAX_SELECTED_BYTES)
    assert _select(engine, [at_limit]).filename == "payload.json"

    over = _zi("payload.json", size=die._ZIP_MAX_SELECTED_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        _select(engine, [over])


# --- streaming ----------------------------------------------------------------


def test_stream_actual_byte_cap(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr(die, "_ZIP_MAX_SELECTED_BYTES", 8)
    info = _zi("payload.json", size=4)  # declared small; actual content larger
    stub = _StubZip([info], streams={"payload.json": io.BytesIO(b"x" * 100)})
    created = _track_mkstemp(monkeypatch)
    with pytest.raises(ValueError, match="streamed size limit"):
        engine._stream_zip_member_to_temp(stub, info)
    assert created and not os.path.exists(created[0])


def test_stream_open_failure_propagates_and_cleans(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    info = _zi("payload.json")
    stub = _StubZip([info], open_exc=RuntimeError("boom"))
    created = _track_mkstemp(monkeypatch)
    fds_before = _fd_count()
    with pytest.raises(RuntimeError, match="boom"):
        engine._stream_zip_member_to_temp(stub, info)
    assert created and not os.path.exists(created[0])
    assert _fd_count() <= fds_before


def test_stream_success_returns_temp_with_content(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    info = _zi("payload.json")
    stub = _StubZip([info], streams={"payload.json": io.BytesIO(b"hello")})
    created = _track_mkstemp(monkeypatch)
    temp_path = engine._stream_zip_member_to_temp(stub, info)
    try:
        assert temp_path == created[0]
        with open(temp_path, "rb") as fh:
            assert fh.read() == b"hello"
    finally:
        engine._best_effort_remove(temp_path)


def test_corrupt_member_normalized_to_valueerror(tmp_path):
    engine = _engine(tmp_path)
    zpath = tmp_path / "corrupt.zip"
    payload = json.dumps({"data": [_VALID_RECORD]}).encode()
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        z.writestr("servers_export.json", payload)
    raw = bytearray(zpath.read_bytes())
    idx = raw.find(b'"ip_address"')
    assert idx != -1
    raw[idx + 2] ^= 0xFF  # corrupt stored data -> CRC mismatch on read
    zpath.write_bytes(raw)
    with pytest.raises(ValueError):
        engine._read_zip_file(str(zpath), None, None)


# --- extractall removed -------------------------------------------------------


def test_extractall_never_called(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    zpath = tmp_path / "ok.zip"
    _make_zip(zpath, [("servers_export.json", json.dumps({"data": [_VALID_RECORD]}))])

    def boom(*args, **kwargs):
        raise AssertionError("extractall must not be called")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", boom)
    data = engine._read_zip_file(str(zpath), None, None)
    assert data == [_VALID_RECORD]


# --- round-trip / identical decision -----------------------------------------


def _export_like_zip(path):
    """Mirror the exporter layout: csv + json payload + metadata member."""
    _make_zip(path, [
        ("servers_export.json", json.dumps({"data": [_VALID_RECORD]})),
        ("servers_export.csv", "ip_address,country,auth_method\n1.1.1.1,US,anonymous\n"),
        ("export_metadata.json", json.dumps({"export_info": {"type": "servers"}})),
    ])


def test_export_like_zip_round_trip(tmp_path):
    engine = _engine(tmp_path)
    zpath = tmp_path / "export.zip"
    _export_like_zip(zpath)

    # Selected payload is the JSON member, not the metadata file.
    data = engine._read_zip_file(str(zpath), "servers", None)
    assert data == [_VALID_RECORD]

    assert engine.validate_file_format(str(zpath))["valid"] is True

    preview = engine.preview_import_data(str(zpath), "servers")
    assert preview["success"] is True
    assert preview["total_records"] == 1

    result = engine.import_data(str(zpath), "servers", validate_only=True)
    assert result["success"] is True
    assert result["records_validated"] == 1


@pytest.mark.parametrize("members", [
    [("dir/x.json", "{}")],                              # nested only
    [("notes.txt", "hello")],                            # no payload
    [("export_metadata.json", "{}")],                    # metadata only
])
def test_identical_reject_decision_across_paths(tmp_path, members):
    engine = _engine(tmp_path)
    zpath = tmp_path / "hostile.zip"
    _make_zip(zpath, members)

    assert engine.validate_file_format(str(zpath))["valid"] is False
    assert engine.preview_import_data(str(zpath), "servers")["success"] is False
    with pytest.raises(ValueError):
        engine.import_data(str(zpath), "servers", validate_only=True)


def test_identical_accept_decision_across_paths(tmp_path):
    engine = _engine(tmp_path)
    zpath = tmp_path / "good.zip"
    _export_like_zip(zpath)

    assert engine.validate_file_format(str(zpath))["valid"] is True
    assert engine.preview_import_data(str(zpath), "servers")["success"] is True
    assert engine.import_data(str(zpath), "servers", validate_only=True)["success"] is True
