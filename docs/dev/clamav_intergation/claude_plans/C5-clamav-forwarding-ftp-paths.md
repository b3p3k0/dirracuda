# C5: Focused Regression + Docs — Implementation Plan

## Context

Cards C2/C3/C4 added FTP and HTTP support to bulk extract (batch + dashboard paths). The prior skip assumptions ("F/H row returns 'not yet supported'") were replaced with probe-gated extraction. C5 closes the remaining coverage gaps and hardens operator docs to reflect the new behavior. No new features; no runtime refactors. Suite baseline: 551 passed, 0 failed in `gui/tests/`.

---

## Gap Analysis

After reading all relevant test files and runtime code, the genuine gaps are:

### Tests missing

| File | Missing test | What it proves |
|------|-------------|----------------|
| `test_action_routing.py` | `test_extract_http_row_empty_snapshot_returns_skipped` | HTTP `{"shares":[]}` → skipped/"Probe snapshot empty"; quarantine dir NOT created |
| `test_action_routing.py` | `test_extract_ftp_port_fallback_from_data` | No `target["port"]` → resolves from `target["data"]["port"]`; runner receives correct port |
| `test_dashboard_bulk_ops.py` | `test_extract_single_server_http_empty_snapshot_skipped_before_quarantine_dir` | Same empty-shares guard, dashboard path |
| `test_extract_runner_clamav.py` | `test_dashboard_single_server_ftp_forwards_clamav_config` | `_extract_single_server` passes `clamav_config` kwarg to `run_ftp_extract` for F rows |
| `test_extract_runner_clamav.py` | `test_batch_execute_extract_ftp_forwards_clamav_config` | `_execute_extract_target` forwards `options["clamav_config"]` to `run_ftp_extract` for F rows |

### Header docstring stale
`test_action_routing.py` line 14: `"- _execute_extract_target: F row returns skipped"` — the file now has 10+ FTP/HTTP extract tests; update to reflect actual coverage.

### Docs missing
- `README.md` "Extracting Files" section doesn't mention FTP/HTTP, probe prerequisite, or the detail popup restriction.
- No C5 artifact in `docs/dev/http_ftp_batch_extract/`.

---

## Implementation

### 1. `gui/tests/test_action_routing.py`

**a. Update header docstring** (line 14): replace
`"- _execute_extract_target: F row returns skipped"`
with
`"- _execute_extract_target: F/H probe-gate (no/empty snapshot → skipped), positive path, cancelled/failed don't mark extracted, SMB unchanged, HTTP empty-snapshot guard, FTP port fallback"`

**b. Add after `test_extract_http_row_missing_port_falls_back_to_db`** (after line ~851):

```python
def test_extract_http_row_empty_snapshot_returns_skipped():
    """HTTP probe snapshot with empty shares -> skipped; quarantine dir NOT created."""
    stub = _BatchMixinStub()
    target = {"ip_address": "1.2.3.4", "host_type": "H", "row_key": "H:8",
              "port": 80, "data": {}}

    with patch("gui.components.server_list_window.actions.batch.create_quarantine_dir") as mock_qdir, \
         patch("gui.utils.http_probe_cache.load_http_probe_result", return_value={"shares": []}):
        result = stub._execute_extract_target("job-1", target, {}, threading.Event())

    assert result["status"] == "skipped"
    assert result["notes"] == "Probe snapshot empty"
    mock_qdir.assert_not_called()


def test_extract_ftp_port_fallback_from_data(tmp_path):
    """FTP target with no top-level port -> resolves from target['data']['port']."""
    stub = _BatchMixinStub()
    target = {
        "ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:7",
        "protocol_server_id": 7, "shares": [],
        "data": {"port": 2121},  # no top-level "port" key
    }
    success_summary = {
        "status": "success", "protocol": "ftp",
        "totals": {"files_downloaded": 0, "bytes_downloaded": 0, "files_skipped": 0},
        "files": [], "errors": [], "timed_out": False, "stop_reason": None,
        "clamav": {"enabled": False},
    }
    captured = {}

    def fake_ftp_extract(ip, port, dl_dir, **kw):
        captured["port"] = port
        return success_summary

    with patch("gui.utils.ftp_probe_cache.load_ftp_probe_result",
               return_value={"shares": [{"directories": [{"name": "pub"}]}]}), \
         patch("gui.components.server_list_window.actions.batch.create_quarantine_dir", return_value=tmp_path), \
         patch("gui.components.server_list_window.actions.batch.extract_runner.run_ftp_extract",
               side_effect=fake_ftp_extract), \
         patch("gui.components.server_list_window.actions.batch.extract_runner.write_extract_log",
               return_value=tmp_path / "log.json"):
        stub._execute_extract_target("job-1", target, {}, threading.Event())

    assert captured.get("port") == 2121, f"Expected port 2121 from data fallback, got {captured.get('port')}"
```

### 2. `gui/tests/test_dashboard_bulk_ops.py`

**Add after `test_extract_single_server_ftp_empty_snapshot_skipped_before_quarantine_dir`** (near line ~609):

```python
def test_extract_single_server_http_empty_snapshot_skipped_before_quarantine_dir(monkeypatch):
    """HTTP empty snapshot (shares=[]) -> skipped with canonical note; quarantine dir NOT created."""
    from unittest.mock import patch
    dash, cancel = _make_dash_with_db()

    monkeypatch.setattr("gui.utils.http_probe_cache.load_http_probe_result",
                        lambda ip, port=None: {"shares": []})

    with patch("gui.components.dashboard.create_quarantine_dir") as mock_qdir, \
         patch("gui.components.dashboard.extract_runner.run_extract") as mock_smb:
        result = dash._extract_single_server(
            {"ip_address": "10.0.0.3", "host_type": "H", "port": 80},
            **_EXTRACT_KWARGS, cancel_event=cancel,
        )

    assert result["status"] == "skipped"
    assert result["notes"] == "Probe snapshot empty"
    mock_qdir.assert_not_called()
    mock_smb.assert_not_called()
```

### 3. `gui/tests/test_extract_runner_clamav.py`

**Append at the end of the file:**

```python
# ---------------------------------------------------------------------------
# C5: ClamAV forwarding — FTP paths (dashboard + batch)
# ---------------------------------------------------------------------------

def test_dashboard_single_server_ftp_forwards_clamav_config(tmp_path, monkeypatch):
    """_extract_single_server passes clamav_config kwarg to run_ftp_extract for F rows."""
    captured: Dict[str, Any] = {}

    def fake_run_ftp_extract(ip, port, download_dir, **kw):
        captured.update(kw)
        return {
            "status": "success",
            "totals": {"files_downloaded": 0, "bytes_downloaded": 0},
            "files": [], "errors": [], "timed_out": False, "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr("gui.components.dashboard.extract_runner.run_ftp_extract",
                        fake_run_ftp_extract)
    monkeypatch.setattr(
        "gui.components.dashboard.create_quarantine_dir",
        lambda *a, **kw: tmp_path,
    )
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.load_ftp_probe_result",
        lambda ip: {"shares": [{"name": "ftp_root"}]},
    )

    from gui.components.dashboard import DashboardWidget
    obj = object.__new__(DashboardWidget)
    obj.db_reader = None

    server = {"ip_address": "1.2.3.4", "host_type": "F", "port": 21}
    clamav_config = {"enabled": True, "backend": "clamscan"}

    import threading
    obj._extract_single_server(
        server, 50, 200, 300, 10, "allow_only", [], [], None,
        threading.Event(), clamav_config,
    )

    assert "clamav_config" in captured, "clamav_config not forwarded to run_ftp_extract"
    assert captured["clamav_config"] == clamav_config


def test_batch_execute_extract_ftp_forwards_clamav_config(tmp_path, monkeypatch):
    """_execute_extract_target forwards options['clamav_config'] to run_ftp_extract for F rows."""
    captured: Dict[str, Any] = {}

    def fake_run_ftp_extract(ip, port, download_dir, **kw):
        captured.update(kw)
        return {
            "status": "success",
            "totals": {"files_downloaded": 0, "bytes_downloaded": 0},
            "files": [], "errors": [], "timed_out": False, "stop_reason": None,
            "clamav": {"enabled": False},
        }

    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.extract_runner.run_ftp_extract",
        fake_run_ftp_extract,
    )
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.create_quarantine_dir",
        lambda *a, **kw: tmp_path,
    )
    monkeypatch.setattr(
        "gui.utils.ftp_probe_cache.load_ftp_probe_result",
        lambda ip: {"shares": [{"name": "ftp_root"}]},
    )
    monkeypatch.setattr(
        "gui.components.server_list_window.actions.batch.extract_runner.write_extract_log",
        lambda s: tmp_path / "log.json",
    )

    # Use the same _BatchMixinStub pattern as test_action_routing.py
    # (importing BatchMixin directly fails — runtime class is ServerListWindowBatchMixin)
    stub = _BatchMixinStub()
    stub._handle_extracted_update = MagicMock()
    target = {
        "ip_address": "1.2.3.4", "host_type": "F", "row_key": "F:7",
        "port": 21, "protocol_server_id": 7, "shares": [], "data": {},
    }
    options = {
        "download_path": str(tmp_path),
        "max_total_size_mb": 100, "max_file_size_mb": 10,
        "max_files_per_target": 50, "max_time_seconds": 60,
        "max_directory_depth": 5, "included_extensions": [],
        "excluded_extensions": [], "download_delay_seconds": 0,
        "connection_timeout": 30, "extension_mode": "allow_only",
        "clamav_config": {"enabled": True, "backend": "clamdscan"},
    }

    import threading
    stub._execute_extract_target("job-1", target, options, threading.Event())

    assert "clamav_config" in captured, "clamav_config not forwarded to run_ftp_extract"
    assert captured["clamav_config"] == {"enabled": True, "backend": "clamdscan"}
```


### 4. `README.md`

**Update "Extracting Files" section** (lines 204-215):

Replace:
```markdown
Automated file collection with configurable limits:

- Max total size
- Max runtime
- Max directory depth
- File extension filtering

All extracted files land in quarantine. The defaults are conservative — check `conf/config.json` if you need to adjust them.
```

With:
```markdown
Automated file collection supporting SMB, FTP, and HTTP open directories, with configurable limits:

- Max total size
- Max runtime
- Max directory depth
- File extension filtering

All extracted files land in quarantine. The defaults are conservative — check `conf/config.json` if you need to adjust them.

**Probe prerequisite (FTP/HTTP):** FTP and HTTP rows must be probed before extract. If no probe snapshot exists, the extract is skipped with a clear note. The detail popup **Extract** button routes through a protocol-aware callback wired from the Server List — it supports SMB, FTP, and HTTP. The `_start_extract` legacy fallback (reached only when no callback is supplied) is SMB-only and will show a redirect message for FTP/HTTP rows.
```

### 5. `docs/dev/http_ftp_batch_extract/C5_regression_docs.md` (new file)

Create evidence artifact documenting:
- Automated validation commands + expected outcomes
- Gap coverage summary (what was added in C5 and why)
- HI test steps (manual validation)

---

## Critical files

| File | Change type |
|------|------------|
| `gui/tests/test_action_routing.py` | Header update + 2 new tests |
| `gui/tests/test_dashboard_bulk_ops.py` | 1 new test |
| `gui/tests/test_extract_runner_clamav.py` | 2 new tests |
| `README.md` | Extracting Files section update |
| `docs/dev/http_ftp_batch_extract/C5_regression_docs.md` | New artifact (created) |

**No runtime code changes** — all runtime paths were verified correct during C2/C3/C4.

---

## Reused functions/utilities

- `_BatchMixinStub` (test_action_routing.py) — reuse for the new batch FTP port fallback test
- `_make_dash_with_db()` + `_EXTRACT_KWARGS` (test_dashboard_bulk_ops.py) — reuse for HTTP empty snapshot test
- `test_batch_execute_extract_forwards_clamav_config_from_options` pattern — mirror for FTP variant

---

## Verification

```bash
# Gate 1: action routing + dashboard bulk ops
./venv/bin/python -m pytest gui/tests/test_action_routing.py gui/tests/test_dashboard_bulk_ops.py -q

# Gate 2: extract runner + browser clamav
./venv/bin/python -m pytest gui/tests/test_extract_runner_clamav.py gui/tests/test_browser_clamav.py -q

# Full suite (only if runtime code changes were required)
xvfb-run -a ./venv/bin/python -m pytest gui/tests/ -q --tb=short
```

Expected result: all tests pass, suite count increases by 5 (2 + 1 + 2 new tests; header edit adds no test).

## HI test needed

Yes.
1. SMB control: batch extract on SMB row → behavior unchanged, summary correct.
2. FTP positive: probe FTP row → batch extract → files appear in quarantine, summary accurate.
3. HTTP positive: probe HTTP row → batch extract → files appear in quarantine, summary accurate.
4. FTP/HTTP negative: pick row with no probe snapshot → batch extract → skipped with "Probe required before FTP/HTTP extract".
5. Dashboard post-scan: run FTP scan with bulk extract enabled → no SMB transport errors on F rows.
