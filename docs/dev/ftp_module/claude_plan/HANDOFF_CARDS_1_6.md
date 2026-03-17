# FTP MVP Handoff (Cards 1-6)

Date: 2026-03-16  
Audience: future agents continuing FTP MVP refinement

## 1. Module Concept

The FTP module is a parallel MVP path to SMB in SMBSeek:

- separate scan launcher and CLI path (`ftpseek`)
- separate persistence sidecar (`ftp_servers`, `ftp_access`)
- separate browse/probe UX (`FtpServerPickerDialog`, `FtpBrowserWindow`)
- shared operator model: discover -> verify -> browse -> quarantine download

Design intent: ship a usable anonymous-FTP workflow quickly without destabilizing SMB or introducing large schema refactors.

## 2. What Cards 1-6 Delivered

### Card 1
- Dashboard split into SMB and FTP scan actions.
- One-active-scan lock semantics preserved.

### Card 2
- FTP CLI/workflow skeleton wired through GUI launch path.
- FTP progress output integrated with existing parser expectations.

### Card 3
- FTP schema and persistence layer added:
  - `ftp_servers`
  - `ftp_access`
  - presence view (`v_host_protocols`) for `has_smb`/`has_ftp`/`both`
- Idempotent migration path maintained.

### Card 4
- FTP discovery reliability pipeline implemented:
  - Shodan candidate query
  - port check
  - anonymous login
  - root listing verification
- Categorized outcomes and reason semantics added.

### Card 5
- FTP probe/browser MVP delivered:
  - `shared/ftp_browser.py` (`FtpNavigator`)
  - `gui/utils/ftp_probe_cache.py`
  - `gui/utils/ftp_probe_runner.py`
  - `gui/components/ftp_browser_window.py`
  - `gui/components/ftp_server_picker.py`
- Dashboard and drill-down wiring added for FTP browser launch.
- Quarantine download flow implemented for FTP files.

### Card 6
- FTP QA and docs hardening pass:
  - `gui/tests/test_ftp_browser.py` (12 tests)
  - `gui/tests/test_ftp_probe.py` (6 tests)
  - `docs/dev/ftp_module/SUMMARY.md` rewritten from planning state to MVP status
- Baseline preserved with no new test failures.

## 3. Commits of Note

- `61b8407` Card 1 scan split placeholder
- `38c2931` FTP skeleton and launch path wiring
- `178e583` Card 3 schema/persistence
- `ff5a9d8` Card 4 discovery reliability
- `aa415a3` Card 5 browser/probe MVP
- `7458d84` Card 6 QA/hardening/docs
- `994efd4` docs correction (`ftp_browser` key naming)

## 4. Current Validation Status

Latest full suite observed:

- `143 passed`
- `2 failed` (pre-existing, unrelated to FTP card work):
  - `gui/tests/test_rce_reporter.py::test_insufficient_data_sets_not_run_status`
  - `gui/tests/test_rce_verdicts.py::test_not_run_statuses`

Latest targeted non-GUI FTP run observed:

- `54 passed`
- `0 failed`

Known warning still present:

- `DeprecationWarning` in `shared/ftp_browser.py` date parsing (LIST format without year context).

## 5. Architecture Quick Map

### Scan path

`Dashboard` -> `scan_manager/interface` -> `ftpseek` -> `shared/ftp_workflow.py` -> `commands/ftp/operation.py` -> `commands/ftp/{shodan_query,verifier}.py` -> `shared/database.py` (`FtpPersistence`)

### Browser path

`Dashboard "FTP Servers"` -> `_open_drill_down("ftp_server_list")` -> `xsmbseek._open_drill_down_window` -> `FtpServerPickerDialog` -> `FtpBrowserWindow` -> `FtpNavigator`

### Probe/cache path

`FtpBrowserWindow._run_probe_background()` -> `run_ftp_probe()` -> `save_ftp_probe_result()` -> `~/.smbseek/ftp_probes/<ip>.json`

## 6. Known Limits (Intentional MVP Boundaries)

- Anonymous FTP only (no credentialed FTP workflows).
- Probe snapshot depth is one level deep.
- No normalized `ftp_files`/`ftp_shares` artifact tables.
- No ranking/value scoring.
- No batch folder download.
- FTP list UI is a lightweight picker, not full SMB server-list parity.
- FTP window focus/restore tracking in `xsmbseek` is not yet implemented.

## 7. Known Risks / Follow-up Targets

1. LIST parser robustness on real-world servers (format drift and locale variance).
2. Close-during-listing GUI race checks (`TclError`) need continued manual validation.
3. Two-connection overlap on some FTP servers (`max_per_ip=1`) is handled non-fatally but can surface probe warnings.
4. Address the LIST parsing deprecation warning in `shared/ftp_browser.py`.

## 8. Operational Notes for Future Agents

1. `docs/dev/` and `gui/tests/` are ignored by `.gitignore` in this repo context; use `git add -f ...` when committing those files.
2. Keep SMB regression as a hard gate whenever touching FTP code paths.
3. Preserve no-new-failures baseline unless explicitly assigned to fix the RCE verdict/reporter tests.
4. Prefer additive/minimal edits around `xsmbseek` drill-down dispatch and dashboard controls.

## 9. Suggested Next Work (Post-MVP)

- Expand parser/unit coverage with fuzzed LIST lines and non-ASCII names.
- Add integration tests with `pyftpdlib` (MLSD enabled and disabled).
- Introduce optional single-instance tracking for FTP picker windows.
- Design normalized FTP artifact schema only when MVP behavior is stable across real deployments.

