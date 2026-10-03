"""Saved bulk-probe limits reach the Self-hosted Search and Reddit probe calls."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.scan_provider_options import resolve_probe_limits


class _Settings:
    def __init__(self, values) -> None:
        self._values = values

    def get_setting(self, key, default=None):
        return self._values.get(key, default)


def test_resolve_probe_limits_defaults_without_settings():
    assert resolve_probe_limits(None) == {
        "workers": 3, "max_dirs": 3, "max_files": 5, "timeout": 10, "max_depth": 1,
    }


def test_resolve_probe_limits_reads_and_clamps_saved_values():
    sm = _Settings({
        "probe.batch_max_workers": 20,
        "probe.max_directories_per_share": 7,
        "probe.max_files_per_directory": "9",
        "probe.share_timeout_seconds": 0,
        "probe.max_depth_levels": 5,
    })
    assert resolve_probe_limits(sm) == {
        "workers": 8, "max_dirs": 7, "max_files": 9, "timeout": 1, "max_depth": 3,
    }


def test_resolve_probe_limits_bad_value_falls_back_per_field():
    sm = _Settings({"probe.max_depth_levels": "deep", "probe.max_directories_per_share": 4})
    limits = resolve_probe_limits(sm)
    assert limits["max_depth"] == 1
    assert limits["max_dirs"] == 4


def test_se_dork_probe_page_rows_passes_limits(monkeypatch):
    from experimental.se_dork import probe as probe_mod
    from experimental.se_dork import service
    from experimental.se_dork.models import RunOptions

    seen = []

    def _fake_probe_url(url, **kwargs):
        seen.append(kwargs)
        return probe_mod.ProbeOutcome(
            probe_status=probe_mod.PROBE_STATUS_CLEAN,
            probe_indicator_matches=0,
            probe_preview=None,
            probe_checked_at="2026-01-01T00:00:00",
            probe_error=None,
        )

    monkeypatch.setattr(probe_mod, "probe_url", _fake_probe_url)
    limits = service._resolve_probe_limits(RunOptions(
        instance_url="http://x", query="q",
        probe_max_directories=6, probe_max_files=8,
        probe_timeout_seconds=20, probe_max_depth=2,
    ))
    service._probe_page_rows(
        1, [{"url": "http://ex.com/", "result_id": 1}],
        config_path=None, indicator_patterns=[], worker_count=1,
        progress_cb=None, probe_limits=limits,
    )
    assert seen and seen[0]["max_directories"] == 6
    assert seen[0]["max_files"] == 8
    assert seen[0]["timeout_seconds"] == 20
    assert seen[0]["max_depth"] == 2


def test_se_dork_limits_default_and_clamp():
    from experimental.se_dork import service
    from experimental.se_dork.models import RunOptions

    assert service._resolve_probe_limits(RunOptions(instance_url="http://x", query="q")) == {
        "max_directories": 3, "max_files": 5, "timeout_seconds": 10, "max_depth": 1,
    }
    limits = service._resolve_probe_limits(
        RunOptions(instance_url="http://x", query="q", probe_max_depth=9, probe_max_files="bad")
    )
    assert limits["max_depth"] == 3
    assert limits["max_files"] == 5


def test_se_dork_probe_url_forwards_depth(monkeypatch):
    from experimental.se_dork import probe as probe_mod

    seen = {}

    def _fake_dispatch(host, host_type, **kwargs):
        seen.update(kwargs)
        return {"shares": [], "errors": []}

    monkeypatch.setattr(probe_mod, "dispatch_probe_run", _fake_dispatch)
    probe_mod.probe_url("http://ex.com/", max_depth=3, max_directories=4)
    assert seen["max_depth"] == 3
    assert seen["max_directories"] == 4


def test_redseek_finalize_passes_limits(monkeypatch):
    from experimental.redseek import service

    seen = {}

    def _fake_probe(keys, db_path, **kwargs):
        seen.update(kwargs)
        return {"total": 0}

    monkeypatch.setattr(service, "_probe_targets_for_keys", _fake_probe)
    opts = service.IngestOptions(
        sort="new", max_posts=5, parse_body=False, include_nsfw=False, replace_cache=False,
        bulk_probe_enabled=True, probe_max_directories=6, probe_max_files=8,
        probe_timeout_seconds=20, probe_max_depth=2,
    )
    result = service.IngestResult.__new__(service.IngestResult)
    result.error = None
    service._finalize_result_with_optional_probe(opts, result, None)
    assert seen["probe_limits"] == {
        "max_directories": 6, "max_files": 8, "timeout_seconds": 20, "max_depth": 2,
    }
