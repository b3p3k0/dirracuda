"""C3 — se_dork threads the HTTP TLS policy through classify and probe."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experimental.se_dork import service
from experimental.se_dork.models import RunOptions


def test_run_options_tls_override_defaults_none():
    opts = RunOptions(instance_url="http://x", query="q")
    assert opts.allow_insecure_tls is None


def test_classify_page_rows_forwards_tls(monkeypatch):
    seen = []

    def _fake_classify(url, timeout=10.0, allow_insecure_tls=None):
        seen.append(allow_insecure_tls)

        class _R:
            verdict = "NOISE"
            reason_code = "x"
            http_status = None

        return _R()

    monkeypatch.setattr("experimental.se_dork.classifier.classify_url", _fake_classify)
    service._classify_page_rows(
        1,
        [{"url": "http://a/"}, {"url": "http://b/"}],
        allow_insecure_tls=False,
    )
    assert seen == [False, False]


def test_probe_page_rows_forwards_tls(monkeypatch):
    seen = []

    def _fake_probe_url(url, *, allow_insecure_tls=None, **kwargs):
        seen.append(allow_insecure_tls)

        class _O:
            probe_status = "clean"
            probe_indicator_matches = 0
            probe_preview = None
            probe_checked_at = "now"
            probe_error = None

        return _O()

    monkeypatch.setattr("experimental.se_dork.probe.probe_url", _fake_probe_url)
    service._probe_page_rows(
        1,
        [{"url": "http://a/", "result_id": 1}],
        config_path=None,
        indicator_patterns=[],
        worker_count=1,
        progress_cb=None,
        allow_insecure_tls=False,
    )
    assert seen == [False]
