Ready for review
Select text to add comments on the plan
C4 -- Scan Queue And CLI Subprocess Runner (rev 3)
Context
C3 (commit ab8b844) delivered auth, sessions, CSRF, login/logout, and a minimal dashboard. C4 adds the ability to launch scans from the web UI: a validated scan request model, a one-active-at-a-time FIFO task queue, a subprocess runner that delegates to the existing CLI entrypoints, and JSON status polling routes. No results display, no DB export, no desktop GUI wiring -- those are C5/C6/C7.

The desktop GUI already uses the same subprocess pattern (process_runner.py, interface.py). C4 mirrors it exactly: merged stderr/stdout, PYTHONUNBUFFERED, repo-root cwd/PYTHONPATH, process-group creation for cancel.

Issue
The web UI has auth and pages but no way to launch or track scans.

Design Reason
CLI as the scan boundary: cli/smbseek.py, cli/ftpseek.py, cli/httpseek.py are the established entrypoints. The desktop GUI already treats these subprocesses as the runtime boundary. The web UI follows the same model.
Argument-list subprocess, explicit shell=False: user input never becomes part of a shell string. All arguments are validated before list construction. shell=False is written explicitly in the Popen call -- not left to the default -- so it cannot be accidentally changed. sys.executable is the interpreter; script paths are computed from __file__ and never from user input.
Mirror process_runner.py subprocess environment exactly (process_runner.py lines 61-96): env = os.environ.copy() + PYTHONUNBUFFERED=1 + PYTHONPATH prepend; cwd=_REPO_ROOT; stderr=subprocess.STDOUT (merged, single stream, no separate drain thread); bufsize=0; POSIX start_new_session=True / Windows CREATE_NEW_PROCESS_GROUP.
No separate stderr_lines field: with stderr=subprocess.STDOUT, there is no separate stderr stream. ScanTask does not carry a stderr_lines field to avoid dead/misleading state. The merged stream is stdout_lines.
One active scan + FIFO deque: matches the desktop app's single-scan model, keeps DB writes serialized.
Conservative progress parsing: unknown stdout lines are log detail, not UI error. Progress percentage clamped to 0-100; out-of-range values are ignored.
CSRF + auth on all mutating routes: reuse existing validate_csrf, same_origin, and get_session from webui/dependencies.py.
run_probe_after_scan: maps to --check-rce (SMB only). FTP and HTTP CLIs have no equivalent flag. The field is rejected with 422 when run_probe_after_scan=True and protocol != "smb". Silent no-op is operator-hostile.
verbose field removed from ScanRequest: --verbose is always passed; it is required for meaningful progress output and the GUI always passes it. Exposing verbose=False that breaks progress parsing would be a lying contract. Field dropped for C4.
Strict Pydantic validation (strict=True): follows C2's guardrail. JSON strings coercing to bool (e.g. "false" -> True) are rejected. Uses ConfigDict(extra="forbid", strict=True).
to_dict() acquires task._lock: status, progress, and stdout_lines are all mutated by the runner thread. The snapshot must be taken under lock.
Process-group termination in both cancel paths: the post-Popen race-guard path uses the same _terminate_proc(proc) helper as normal cancel. Both paths apply POSIX os.killpg(SIGTERM) / Windows CTRL_BREAK_EVENT with fallback to proc.terminate().
Country validation applied to all protocols: FTP/HTTP CLI parsers do not validate country codes at argparse level. The web tier applies the full ISO 3166-1 alpha-2 allowlist to all three protocols as a deliberate guardrail. The set is duplicated in tasks.py (not imported from cli.smbseek) because cli/smbseek.py has module-level side effects (_PATHS = get_paths(), sys.path manipulation) that are wrong to trigger in a web request context. A carry-forward note in the source requires alignment if the CLI set changes.
Filter control-character rejection: all characters with ord(c) < 32 are rejected -- not just \x00 and \r. LF and other control chars can confuse command output and log contexts.
Proposed Plan
Step 0 -- save the approved plan
Before writing any product code, save this document as:

docs/dev/webui/approved_plans/C4_scan_queue_and_cli_subprocess_runner.md
This is a carry-forward requirement for every card (C0 precedent).

New file: webui/tasks.py
Imports and constants (module top)

_REPO_ROOT = Path(__file__).parent.parent
_PROTOCOL_SCRIPTS = {
    "smb":  _REPO_ROOT / "cli" / "smbseek.py",
    "ftp":  _REPO_ROOT / "cli" / "ftpseek.py",
    "http": _REPO_ROOT / "cli" / "httpseek.py",
}
_CONFIG_PATH = _REPO_ROOT / "conf" / "config.json"

# keep aligned with cli/smbseek.py::validate_country_codes
_VALID_COUNTRY_CODES = frozenset({
    'AD','AE','AF','AG','AI','AL','AM','AO','AQ','AR','AS','AT',
    # ... full ISO 3166-1 alpha-2 set (249 codes, identical to smbseek.py)
})

_PROGRESS_RE = re.compile(r'\b(\d{1,3})%')
class TaskStatus(str, enum.Enum) Values: queued, running, done, cancelled, failed.

class CancelResult(str, enum.Enum) Values: ok, not_found, terminal.

class ScanRequest(BaseModel) -- Pydantic v2 with strict validation

model_config = ConfigDict(extra="forbid", strict=True)
Field	Type	Default	Validation
protocol	str	--	in {"smb", "ftp", "http"}
countries	list[str]	Field(default_factory=list)	each in _VALID_COUNTRY_CODES; empty = global
run_probe_after_scan	bool	False	model-level: True + non-SMB -> ValidationError; strict=True rejects string coercion
filters	str	""	len <= 500; no character with ord(c) < 32
@field_validator("protocol")
@classmethod
def _validate_protocol(cls, v: str) -> str:
    if v not in {"smb", "ftp", "http"}:
        raise ValueError(f"protocol must be smb, ftp, or http; got {v!r}")
    return v

@field_validator("countries")
@classmethod
def _validate_countries(cls, v: list[str]) -> list[str]:
    bad = [c for c in v if c.upper() not in _VALID_COUNTRY_CODES]
    if bad:
        raise ValueError(f"invalid country code(s): {bad}")
    return [c.upper() for c in v]

@field_validator("filters")
@classmethod
def _validate_filters(cls, v: str) -> str:
    if len(v) > 500:
        raise ValueError("filters must not exceed 500 characters")
    if any(ord(c) < 32 for c in v):
        raise ValueError("filters contain invalid control characters")
    return v

@model_validator(mode="after")
def _probe_protocol_check(self) -> "ScanRequest":
    if self.run_probe_after_scan and self.protocol != "smb":
        raise ValueError(
            "run_probe_after_scan is only supported for protocol 'smb'; "
            f"got {self.protocol!r}"
        )
    return self
@dataclass class ScanTask

task_id: str                              # secrets.token_hex(8)
request: ScanRequest
status: TaskStatus = TaskStatus.QUEUED
stdout_lines: list[str] = field(default_factory=list)  # merged stdout+stderr stream
progress_pct: float = 0.0
progress_message: str = ""
created_at: float = field(default_factory=time.time)
started_at: Optional[float] = None
finished_at: Optional[float] = None
_process: Optional[subprocess.Popen] = field(default=None, repr=False)
_cancel_requested: bool = field(default=False, repr=False)
_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
to_dict() -- acquires task._lock, takes a consistent snapshot:

def to_dict(self) -> dict:
    with self._lock:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "protocol": self.request.protocol,
            "countries": self.request.countries,
            "progress_pct": self.progress_pct,
            "progress_message": self.progress_message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": self.stdout_lines[-100:],   # copy under lock
        }
build_command(request: ScanRequest) -> list[str] -- module-level, pure function

cmd = [sys.executable, str(_PROTOCOL_SCRIPTS[request.protocol]),
       "--verbose",                          # always; required for progress output
       "--config", str(_CONFIG_PATH)]
if request.countries:
    cmd += ["--country", ",".join(request.countries)]
if request.filters:
    cmd += ["--filter", request.filters]
if request.run_probe_after_scan:             # only smb reaches here (model validator)
    cmd.append("--check-rce")
return cmd
Never contains a shell string. Never uses shell=True.

_build_subprocess_env() -> dict -- module-level helper

env = os.environ.copy()
env["PYTHONUNBUFFERED"] = "1"
existing = env.get("PYTHONPATH", "")
env["PYTHONPATH"] = f"{_REPO_ROOT}:{existing}".rstrip(":")
return env
Mirrors process_runner.py lines 61-68.

_terminate_proc(proc: subprocess.Popen) -> None -- module-level helper

Shared by the normal cancel path and the post-Popen race guard. Applies process-group termination (SIGTERM) on POSIX and CTRL_BREAK_EVENT on Windows, with fallback to proc.terminate() if the process group is unavailable.

if sys.platform.startswith("win"):
    try: proc.send_signal(signal.CTRL_BREAK_EVENT)
    except (ProcessLookupError, OSError): proc.terminate()
else:
    try: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError): proc.terminate()
class ScanQueue

Internal state (protected by self._lock, a threading.Lock):

_tasks: dict[str, ScanTask]
_active: Optional[ScanTask]
_queue: deque[ScanTask]
Public interface:

submit(request: ScanRequest) -> ScanTask
cancel(task_id: str) -> CancelResult
get_task(task_id: str) -> Optional[ScanTask]
queue_status() -> dict
Private:

_start(task) -- under self._lock; sets RUNNING, starts daemon thread
_advance() -- under self._lock; pops _queue, calls _start
_run(task) -- daemon thread body
_kill_after(proc, timeout_s) -- watchdog daemon thread
_run(task) thread body:

1.  # Pre-Popen cancel check
    under task._lock:
        if task._cancel_requested:
            task.status = CANCELLED; task.finished_at = now
    under self._lock:
        self._active = None; self._advance()
    return   # exit without launching subprocess

2.  cmd = build_command(task.request)
    env = _build_subprocess_env()
    if POSIX:
        proc = Popen(cmd, cwd=str(_REPO_ROOT),
                     stdout=PIPE, stderr=STDOUT,
                     text=True, bufsize=0, shell=False,
                     env=env, start_new_session=True)
    if Windows:
        proc = Popen(cmd, cwd=str(_REPO_ROOT),
                     stdout=PIPE, stderr=STDOUT,
                     text=True, bufsize=0, shell=False, env=env,
                     creationflags=CREATE_NEW_PROCESS_GROUP)

3.  # Post-Popen cancel check; uses _terminate_proc for process-group termination
    under task._lock:
        task._process = proc
        if task._cancel_requested:
            _terminate_proc(proc)
            spawn _kill_after(proc, 10) daemon

4.  for line in proc.stdout:   # drains merged stream until EOF
        _parse_progress(task, line.rstrip("\n"))

5.  proc.wait()                 # returncode; process already exited at EOF

6.  under task._lock:
        if task._cancel_requested:  status = CANCELLED
        elif proc.returncode == 0:  status = DONE
        else:                       status = FAILED
        task.finished_at = now

7.  under self._lock:
        self._active = None
        self._advance()
On any exception: FAILED, finished_at, clear _active, _advance().

cancel(task_id) -- returns CancelResult:

under self._lock:
    task = _tasks.get(task_id)
    if task is None:
        return CancelResult.NOT_FOUND
    if task.status in {DONE, CANCELLED, FAILED}:
        return CancelResult.TERMINAL
    if task.status == QUEUED:
        _queue.remove(task)           # O(n), queue is short
        under task._lock: status=CANCELLED, finished_at=now
        return CancelResult.OK
    if task.status == RUNNING:
        under task._lock: _cancel_requested=True, get proc ref
        if proc is not None:
            _terminate_proc(proc)
            spawn _kill_after(proc, 10) daemon
        # If proc is None (pre-Popen gap), _cancel_requested causes _run to
        # bail at step 1 or step 3 without launching work.
        return CancelResult.OK
_kill_after(proc, timeout):

POSIX:
    try: proc.wait(timeout)
    except TimeoutExpired:
        try: os.killpg(os.getpgid(proc.pid), SIGKILL)
        except (ProcessLookupError, OSError): proc.kill()
Windows:
    try: proc.wait(timeout)
    except TimeoutExpired: proc.terminate()
_parse_progress(task, line) -- module-level helper:

Under task._lock: append line to task.stdout_lines. If _PROGRESS_RE.search(line) matches and 0 <= int(m.group(1)) <= 100, update task.progress_pct. Out-of-range values (e.g. 125%) are ignored without error. Unknown lines are silently logged as detail.

Modified file: webui/app.py
In create_app() after app.state.session_store = SessionStore():

from webui.tasks import ScanQueue, ScanRequest, CancelResult
app.state.scan_queue = ScanQueue()
4 new routes:

GET  /scans                       -> scans.html  (Depends(get_session))
POST /api/scans                   -> submit scan (same_origin + validate_csrf + get_session)
                                    body: ScanRequest; returns 202 {task_id, status}
GET  /api/scans/{task_id}         -> poll status (get_session)
                                    200 task.to_dict()  or  404
POST /api/scans/{task_id}/cancel  -> cancel (same_origin + validate_csrf + get_session)
                                    CancelResult.NOT_FOUND -> 404 {error: "not found"}
                                    CancelResult.TERMINAL  -> 409 {ok:false, status:<value>}
                                    CancelResult.OK        -> 200 {ok:true}
Logged (no secrets, no filter content):

logger.info("scan submitted: task_id=%s protocol=%s", ...)
logger.info("scan cancel requested: task_id=%s", ...)
Modified file: webui/templates/dashboard.html
Add a "Launch Scan" link so /scans is reachable without knowing the URL:

<p><a href="/scans">Launch Scan</a></p>
Placed after the logged-in username line, before the logout button. One-line addition; template goes from 25 to 26 lines.

New file: webui/templates/scans.html
Minimal server-rendered form (same structure as dashboard.html):

<meta name="csrf-token" content="{{ session.csrf_token }}"> in <head>
Protocol <select>: smb / ftp / http
Countries <input type="text"> -- placeholder uses only ASCII: US,GB - leave blank for global
"Run post-scan probe (SMB only)" <input type="checkbox">
Filters <input type="text"> (optional)
Submit <button>
Status <div> updated by JS after POST
Inline <script> responsibilities:

Read CSRF from meta tag.
Parse countries input: split(","), trim whitespace, uppercase, discard blank entries. Prevents avoidable validation errors from "US, GB" input.
POST JSON {protocol, countries, run_probe_after_scan, filters} to /api/scans with X-CSRF-Token header.
On 202: display task_id and status in the status div.
On 422: display the validation error message from the response body.
No non-ASCII characters anywhere in the template.
No framework, no build step.
New file: webui/tests/test_tasks.py
Unit tests -- subprocess.Popen monkeypatched where needed:

Test	What it checks
test_build_command_smb	sys.executable, smbseek.py path, --verbose, --config in list
test_build_command_ftp	ftpseek.py in cmd
test_build_command_http	httpseek.py in cmd
test_build_command_probe_smb	run_probe_after_scan=True, protocol="smb" -> --check-rce in list
test_build_command_no_country	countries=[] -> --country absent
test_build_command_no_filter	filters="" -> --filter absent
test_build_subprocess_env	PYTHONUNBUFFERED=1 in env; str(_REPO_ROOT) in PYTHONPATH
test_validate_bad_protocol	ScanRequest(protocol="ssh") -> ValidationError
test_validate_probe_ftp_rejected	run_probe_after_scan=True, protocol="ftp" -> ValidationError
test_validate_probe_http_rejected	same for http
test_validate_bad_country	countries=["ZZ"] -> ValidationError
test_validate_filter_too_long	filters="x"*501 -> ValidationError
test_validate_filter_control_chars	filters with \x00, \r, \n, \x1f each -> ValidationError
test_validate_extra_fields_rejected	ScanRequest(..., extra="evil") -> ValidationError
test_validate_bool_strict_rejects_string	ScanRequest(protocol="smb", run_probe_after_scan="false") -> ValidationError
test_validate_countries_default_factory	two ScanRequest() instances do not share list state
test_validate_verbose_field_absent	ScanRequest has no verbose field
test_task_initial_state	status=QUEUED, stdout_lines=[], pct=0.0, no stderr_lines attr
test_task_to_dict_no_private_fields	_process, _lock, _cancel_requested not in keys
test_task_to_dict_snapshot_under_lock	mutate task after to_dict() call; snapshot is not affected
test_popen_called_with_correct_args	monkeypatch subprocess.Popen; assert shell=False (explicit kwarg), args is a list, cwd==str(_REPO_ROOT), "PYTHONUNBUFFERED" in env, stderr==subprocess.STDOUT
test_queue_submit_starts_when_idle	first submit -> status RUNNING
test_queue_submit_queues_when_active	second submit while first runs -> QUEUED
test_queue_fifo_order	second task becomes RUNNING after first completes
test_queue_cancel_queued_returns_ok	queued task -> CancelResult.OK, status CANCELLED
test_queue_cancel_running_returns_ok	running task -> CancelResult.OK, terminate called
test_queue_cancel_done_returns_terminal	terminal task -> CancelResult.TERMINAL
test_queue_cancel_unknown_returns_not_found	-> CancelResult.NOT_FOUND
test_cancel_race_before_process_assigned	_cancel_requested=True before _process assigned; _run exits CANCELLED without Popen
test_parse_progress_known_pattern	"[25%] discovering" -> pct=25.0, line in stdout_lines
test_parse_progress_unknown_line	unrecognized line appended, no error
test_parse_progress_out_of_range	"125%" in line -> pct not updated, no error
test_parse_progress_clamps_to_100	"100%" -> pct=100.0 accepted; "101%" -> pct unchanged
New file: webui/tests/test_scan_routes.py
Route integration tests using fastapi.testclient.TestClient (same pattern as test_login.py). The ScanQueue instance is replaced on app.state before TestClient construction:

app = create_app(cfg=cfg_no_tls, creds_path=creds)
app.state.scan_queue = FakeScanQueue()   # inject fake; no subprocess launched
client = TestClient(app, follow_redirects=False)
FakeScanQueue returns deterministic ScanTask stubs with controllable CancelResult return values.

Test	What it checks
test_scans_page_requires_auth	GET /scans unauth -> 303 /login
test_scans_page_authenticated	GET /scans with session -> 200
test_submit_requires_auth	POST /api/scans unauth -> 303
test_submit_missing_csrf	auth + no X-CSRF-Token -> 403
test_submit_bad_origin	auth + Origin: http://attacker.com -> 403
test_submit_invalid_protocol	protocol="ssh" -> 422
test_submit_invalid_country	countries=["ZZ"] -> 422
test_submit_probe_on_ftp	run_probe_after_scan=True, protocol="ftp" -> 422
test_submit_probe_string_coercion_rejected	run_probe_after_scan="false" -> 422
test_submit_valid_smb	valid smb -> 202, task_id in body
test_submit_valid_ftp	valid ftp -> 202
test_submit_valid_http	valid http -> 202
test_get_scan_requires_auth	unauth -> 303
test_get_scan_not_found	authenticated, unknown id -> 404
test_get_scan_valid	submit + poll same id -> 200, status field present
test_cancel_requires_auth	unauth -> 303
test_cancel_missing_csrf	auth + no CSRF -> 403
test_cancel_not_found	fake returns NOT_FOUND -> 404
test_cancel_terminal_task	fake returns TERMINAL -> 409 {ok:false}
test_cancel_valid	fake returns OK -> 200 {ok:true}
Files Expected To Change
File	Change	Direction
docs/dev/webui/approved_plans/C4_scan_queue_and_cli_subprocess_runner.md	new -- approved plan saved before product code	new
webui/tasks.py	new -- ~260 lines	new
webui/app.py	+4 routes + ScanQueue wiring -- 133 -> ~190 lines	modify
webui/templates/dashboard.html	+1 line (scans link) -- 25 -> 26 lines	modify
webui/templates/scans.html	new -- ~50 lines	new
webui/tests/test_tasks.py	new -- ~280 lines	new
webui/tests/test_scan_routes.py	new -- ~130 lines	new
No changes to: webui/sessions.py, webui/dependencies.py, webui/config.py, webui/auth.py, webui/server.py, webui/requirements-web.txt, any gui/ or cli/ files.

Validation Planned
# 1. Syntax check
./venv/bin/python -m py_compile webui/tasks.py webui/app.py

# 2. Targeted C4 tests
./venv/bin/python -m pytest webui/tests/test_tasks.py webui/tests/test_scan_routes.py -v

# 3. Full webui suite (C1-C4; all C3 tests must continue to pass)
./venv/bin/python -m pytest webui/tests/ -q

# 4. Dependency audit
./venv/bin/python -m pip check

# 5. Confirm pre-existing failure unchanged
./venv/bin/python -m pytest \
  gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success -q
# expect: 1 failure (pre-existing, do not fix in C4)

# 6. Line counts before/after
wc -l webui/app.py webui/templates/dashboard.html
# before: 133 / 25; after: ~190 / 26

# 7. README.md and docs/TECHNICAL_REFERENCE.md review
# Re-read both after implementation. If C4 makes either inaccurate (e.g. scan
# behavior now exists but the doc says none is present), update. If both remain
# accurate, document the review result in the handoff and note what C9 will add.

# 8. __pycache__ check before declaring done
find webui cli commands -name "*.pyc" -newer webui/tasks.py 2>/dev/null | head -5
# confirm none are staged
Sources
Python subprocess docs: https://docs.python.org/3/library/subprocess.html -- Popen, explicit shell=False, merged stderr, start_new_session, process groups, TimeoutExpired, bufsize=0.
OWASP OS Command Injection Defense Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html -- argument list parameterization, allowlist validation, reject control chars.
Desktop subprocess pattern: gui/utils/backend_interface/process_runner.py lines 60-96 (env, cwd, stderr=STDOUT, process group, bufsize=0).
Cancel pattern: process_runner.py lines 188-256 (POSIX killpg, Windows CTRL_BREAK_EVENT, bounded wait, fallback to direct kill).
C3 approved plan + webui/app.py C3 state -- route/CSRF/auth patterns reused.
C2 approved plan -- ConfigDict(extra="forbid", strict=True) guardrail.
AI Agent Field Guide + Development Guide -- subprocess safety, scope control.
AI Agent Doc Style Guide URL returned 404 on fetch; project conventions from existing webui approved plans applied instead.
Risks / Blockers
run_probe_after_scan for FTP/HTTP is a 422 -- model validator enforces this. If RA/HI later wants to accept-but-warn instead of reject, that is a design change.

ISO country set duplication -- tasks.py must stay aligned with cli/smbseek.py::validate_country_codes. Carry-forward note in source.

conf/config.json may not exist in test environments -- build_command constructs the path but does not check existence. Route tests inject FakeScanQueue; test_popen_called_with_correct_args monkeypatches Popen.

Process-group kill on POSIX -- os.killpg(os.getpgid(proc.pid), SIGKILL) can raise ProcessLookupError if the process exits between TimeoutExpired and the kill. _kill_after wraps in try/except (ProcessLookupError, OSError) with fallback to proc.kill().

deque.remove() cancel for QUEUED tasks -- O(n). Queue expected very short in v1.

CredentialError in webui/auth.py -- unused, still deferred. C4 does not touch auth.

Pre-existing failure: gui/tests/test_server_ops_scenario_matrix.py::test_s10_se_dork_probe_task_lifecycle_success is pre-existing. C4 does not touch the relevant code; expect it remains the only failure in the quick lane.