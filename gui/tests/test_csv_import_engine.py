"""
Unit tests for gui.utils.csv_import_engine pure helper functions.

These tests cover extracted logic that is independent of a real database
fixture, providing fast, focused coverage for the normalization and coercion
layer.
"""

import csv
import os
import tempfile

import pytest

from gui.utils.csv_import_engine import (
    coerce_bool,
    coerce_db_timestamp,
    coerce_int,
    normalize_csv_column_name,
    normalize_host_type,
    read_csv_host_records,
)


# ---------------------------------------------------------------------------
# normalize_csv_column_name
# ---------------------------------------------------------------------------

class TestNormalizeCsvColumnName:
    def test_lowercases(self):
        assert normalize_csv_column_name("IP_Address") == "ip_address"

    def test_strips_whitespace(self):
        assert normalize_csv_column_name("  country  ") == "country"

    def test_replaces_hyphens(self):
        assert normalize_csv_column_name("last-seen") == "last_seen"

    def test_replaces_spaces(self):
        assert normalize_csv_column_name("auth method") == "auth_method"

    def test_mixed(self):
        assert normalize_csv_column_name(" Host-Type ") == "host_type"


# ---------------------------------------------------------------------------
# normalize_host_type
# ---------------------------------------------------------------------------

class TestNormalizeHostType:
    def test_none_returns_s(self):
        assert normalize_host_type(None) == 'S'

    def test_empty_string_returns_s(self):
        assert normalize_host_type('') == 'S'

    def test_whitespace_returns_s(self):
        assert normalize_host_type('   ') == 'S'

    def test_s_variants(self):
        for v in ('S', 's', 'SMB', 'smb', 'Smb'):
            assert normalize_host_type(v) == 'S', f"failed for {v!r}"

    def test_f_variants(self):
        for v in ('F', 'f', 'FTP', 'ftp'):
            assert normalize_host_type(v) == 'F', f"failed for {v!r}"

    def test_h_variants(self):
        for v in ('H', 'h', 'HTTP', 'http', 'HTTPS', 'https'):
            assert normalize_host_type(v) == 'H', f"failed for {v!r}"

    def test_unknown_returns_none(self):
        # Invalid values must return None so CSV analysis can skip the row.
        assert normalize_host_type('X') is None
        assert normalize_host_type('SFTP') is None
        assert normalize_host_type('ssh') is None


# ---------------------------------------------------------------------------
# coerce_int
# ---------------------------------------------------------------------------

class TestCoerceInt:
    def test_integer_string(self):
        assert coerce_int('42', default=0) == 42

    def test_float_string(self):
        assert coerce_int('21.0', default=0) == 21

    def test_none_returns_default(self):
        assert coerce_int(None, default=5) == 5

    def test_empty_string_returns_default(self):
        assert coerce_int('', default=3) == 3

    def test_bad_value_returns_default(self):
        assert coerce_int('banana', default=7) == 7

    def test_minimum_enforced(self):
        assert coerce_int('0', default=1, minimum=1) == 1

    def test_value_above_minimum_accepted(self):
        assert coerce_int('10', default=1, minimum=1) == 10


# ---------------------------------------------------------------------------
# coerce_bool
# ---------------------------------------------------------------------------

class TestCoerceBool:
    def test_truthy_strings(self):
        for v in ('1', 'true', 'True', 'TRUE', 'yes', 'YES', 'y', 'Y', 'on', 'ON'):
            assert coerce_bool(v) is True, f"expected True for {v!r}"

    def test_falsy_strings(self):
        for v in ('0', 'false', 'False', 'no', 'NO', 'n', 'N', 'off', 'OFF'):
            assert coerce_bool(v) is False, f"expected False for {v!r}"

    def test_none_returns_default(self):
        assert coerce_bool(None) is False
        assert coerce_bool(None, default=True) is True

    def test_bool_passthrough(self):
        assert coerce_bool(True) is True
        assert coerce_bool(False) is False

    def test_unknown_string_returns_default(self):
        assert coerce_bool('maybe') is False
        assert coerce_bool('maybe', default=True) is True


# ---------------------------------------------------------------------------
# coerce_db_timestamp
# ---------------------------------------------------------------------------

class TestCoerceDbTimestamp:
    def test_valid_iso_truncated_to_19(self):
        result = coerce_db_timestamp('2026-03-15 14:23:45.123456', fallback='X')
        assert result == '2026-03-15 14:23:45'

    def test_already_19_chars(self):
        result = coerce_db_timestamp('2026-01-01 00:00:00', fallback='X')
        assert result == '2026-01-01 00:00:00'

    def test_empty_string_returns_fallback(self):
        result = coerce_db_timestamp('', fallback='2026-01-01 00:00:00')
        assert result == '2026-01-01 00:00:00'

    def test_none_returns_fallback(self):
        result = coerce_db_timestamp(None, fallback='2026-01-01 00:00:00')
        assert result == '2026-01-01 00:00:00'


# ---------------------------------------------------------------------------
# read_csv_host_records
# ---------------------------------------------------------------------------

class TestReadCsvHostRecords:
    def _write_csv(self, lines: list) -> str:
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    def test_basic_read(self):
        path = self._write_csv([
            'ip_address,host_type,country',
            '1.2.3.4,S,US',
            '5.6.7.8,F,DE',
        ])
        try:
            records = read_csv_host_records(path)
        finally:
            os.unlink(path)

        assert len(records) == 2
        row_num, row = records[0]
        assert row_num == 2
        assert row['ip_address'] == '1.2.3.4'
        assert row['host_type'] == 'S'

    def test_comment_lines_skipped(self):
        path = self._write_csv([
            '# This is a comment',
            'ip_address,host_type',
            '# another comment',
            '10.0.0.1,S',
        ])
        try:
            records = read_csv_host_records(path)
        finally:
            os.unlink(path)

        assert len(records) == 1
        assert records[0][1]['ip_address'] == '10.0.0.1'

    def test_header_normalized_to_snake_case(self):
        path = self._write_csv([
            'IP-Address,Host Type',
            '1.1.1.1,S',
        ])
        try:
            records = read_csv_host_records(path)
        finally:
            os.unlink(path)

        assert len(records) == 1
        row = records[0][1]
        assert 'ip_address' in row
        assert 'host_type' in row

    def test_blank_rows_skipped(self):
        path = self._write_csv([
            'ip_address,host_type',
            '2.2.2.2,S',
            ',',
            '3.3.3.3,F',
        ])
        try:
            records = read_csv_host_records(path)
        finally:
            os.unlink(path)

        assert len(records) == 2

    def test_missing_ip_address_column_raises_with_message(self):
        """Error message must contain 'ip_address' (contract used by tests in test_db_tools_engine)."""
        path = self._write_csv([
            'ip,host_type',
            '1.1.1.1,S',
        ])
        try:
            with pytest.raises(ValueError) as exc_info:
                read_csv_host_records(path)
        finally:
            os.unlink(path)

        assert 'ip_address' in str(exc_info.value)
