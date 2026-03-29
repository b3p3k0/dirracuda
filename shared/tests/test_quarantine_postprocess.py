"""Unit tests for shared/quarantine_postprocess.py and its seam in extract_runner.

Contract tests (tests 1-5): pure Python, no network, no impacket.
Seam injection tests (tests 6-8): monkeypatched SMBConnection, no real network.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shared.quarantine_postprocess import (
    PostProcessInput,
    PostProcessResult,
    passthrough_processor,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input(file_path: Path) -> PostProcessInput:
    return PostProcessInput(
        file_path=file_path,
        ip_address="1.2.3.4",
        share="pub",
        rel_display="sub/file.txt",
        file_size=42,
    )


def _fake_conn():
    """Fake SMBConnection serving one file: pub/a.txt (3 bytes)."""
    entry = MagicMock()
    entry.get_longname.return_value = "a.txt"
    entry.is_directory.return_value = False
    entry.get_filesize.return_value = 3
    entry.get_mtime_epoch.return_value = None

    conn = MagicMock()
    conn.listPath.return_value = [entry]
    conn.getFile.side_effect = lambda share, smb_path, writer: writer(b"abc")
    return conn


def _run_extract(tmp_path, monkeypatch, **kwargs):
    """Call run_extract with a monkeypatched SMBConnection."""
    from gui.utils.extract_runner import run_extract

    monkeypatch.setattr(
        "gui.utils.extract_runner.SMBConnection",
        lambda *a, **kw: _fake_conn(),
    )
    download_dir = tmp_path / "quarantine" / "1.2.3.4" / "20260328"
    return run_extract(
        "1.2.3.4",
        ["pub"],
        download_dir=download_dir,
        username="",
        password="",
        max_total_bytes=0,
        max_file_bytes=0,
        max_file_count=0,
        max_seconds=0,
        max_depth=3,
        allowed_extensions=[],
        denied_extensions=[],
        delay_seconds=0,
        connection_timeout=5,
        **kwargs,
    ), download_dir


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

def test_passthrough_returns_skipped_verdict(tmp_path):
    f = tmp_path / "file.txt"
    f.write_bytes(b"x")
    inp = _make_input(f)
    result = passthrough_processor(inp)
    assert result.verdict == "skipped"
    assert result.moved is False
    assert result.final_path == f
    assert result.metadata is None
    assert result.error is None


def test_passthrough_destination_is_quarantine(tmp_path):
    f = tmp_path / "file.txt"
    f.write_bytes(b"x")
    result = passthrough_processor(_make_input(f))
    assert result.destination == "quarantine"


def test_passthrough_does_not_move_file(tmp_path):
    f = tmp_path / "file.txt"
    f.write_bytes(b"x")
    passthrough_processor(_make_input(f))
    assert f.exists()


def test_postprocess_result_fields_accessible(tmp_path):
    f = tmp_path / "file.txt"
    r = PostProcessResult(
        final_path=f,
        verdict="skipped",
        moved=False,
        destination="quarantine",
        metadata=None,
        error=None,
    )
    assert r.final_path == f
    assert r.verdict == "skipped"
    assert r.moved is False
    assert r.destination == "quarantine"
    assert r.metadata is None
    assert r.error is None


def test_postprocess_input_fields_accessible(tmp_path):
    f = tmp_path / "file.txt"
    inp = PostProcessInput(
        file_path=f,
        ip_address="10.0.0.1",
        share="docs",
        rel_display="readme.txt",
        file_size=100,
    )
    assert inp.file_path == f
    assert inp.ip_address == "10.0.0.1"
    assert inp.share == "docs"
    assert inp.rel_display == "readme.txt"
    assert inp.file_size == 100


# ---------------------------------------------------------------------------
# Seam injection tests
# ---------------------------------------------------------------------------

def test_run_extract_saved_to_uses_postprocessor_final_path(tmp_path, monkeypatch):
    """post_processor.final_path is reflected in summary saved_to."""
    sentinel = tmp_path / "redirected.txt"

    def redirecting_processor(inp: PostProcessInput) -> PostProcessResult:
        return PostProcessResult(
            final_path=sentinel,
            verdict="clean",
            moved=True,
            destination="extracted",
            metadata=None,
            error=None,
        )

    summary, _ = _run_extract(tmp_path, monkeypatch, post_processor=redirecting_processor)

    assert len(summary["files"]) == 1
    assert summary["files"][0]["saved_to"] == str(sentinel)


def test_run_extract_saved_to_unchanged_without_postprocessor(tmp_path, monkeypatch):
    """Without post_processor, saved_to is the original quarantine path."""
    summary, download_dir = _run_extract(tmp_path, monkeypatch)

    assert len(summary["files"]) == 1
    expected = str(download_dir / "pub" / "a.txt")
    assert summary["files"][0]["saved_to"] == expected


def test_run_extract_postprocessor_exception_is_failopen(tmp_path, monkeypatch):
    """A raising post_processor falls back to original path and records an error."""
    def raising_processor(inp: PostProcessInput) -> PostProcessResult:
        raise RuntimeError("boom")

    summary, download_dir = _run_extract(tmp_path, monkeypatch, post_processor=raising_processor)

    # File still recorded at original quarantine path
    assert len(summary["files"]) == 1
    expected = str(download_dir / "pub" / "a.txt")
    assert summary["files"][0]["saved_to"] == expected

    # Error recorded in summary
    pp_errors = [e for e in summary["errors"] if "post_processor error" in e.get("message", "")]
    assert pp_errors, "Expected a post_processor error entry in summary['errors']"
    assert "boom" in pp_errors[0]["message"]
