"""
Bounded bubblewrap smoke checks.

DISPOSITION: RETAINED as immutable C0B Stage-A evidence after C3. This is a
measurement instrument, not the production sandbox, and production never imports it.
It is explicitly NOT the Stage E extraction boundary - it proves the mechanism works
on this host, nothing more.

Checks (CONTRACT.md §5): network unreachable, host HOME absent, repository not
bound, rlimits enforced via prlimit (never preexec_fn - the worker is threaded),
process-group kill on timeout, antiword runs sandboxed, and PyMuPDF imports and
extracts text from one benign PDF inside the sandbox.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

# Resource limits for a one-file parser child.
RLIMIT_AS_BYTES = 1 << 30           # 1 GiB address space
RLIMIT_CPU_S = 20
RLIMIT_NOFILE = 64
RLIMIT_CORE = 0

# RLIMIT_NPROC is deliberately NOT applied here.
#
# Measured on this host: it is a PER-UID limit counting every process the user
# already owns (227 at time of measurement), so `prlimit --nproc=64` makes
# bwrap's clone() fail with EAGAIN before the sandbox is ever created. Any value
# low enough to bound a fork bomb is also low enough to stop the sandbox
# starting; any value high enough to start it is not a bound.
#
# CONTRACT.md §5 asks for a process-count limit; the correct mechanism on this
# platform is a cgroup `pids.max`, not RLIMIT_NPROC. C3 owns that. Until then
# the fork-bomb controls actually in force are the PID namespace and the
# process-group kill. Recorded rather than papered over.
RLIMIT_NPROC_DEFERRED_TO_C3 = True


@dataclass
class SmokeCheck:
    name: str
    ok: bool
    detail: str


def _prlimit_prefix() -> List[str]:
    """Applied INSIDE the sandbox, after namespace creation.

    prlimit must not wrap bwrap itself: the limits would land on namespace
    setup rather than on the parser, and RLIMIT_NPROC would abort it outright.
    Never `preexec_fn` either - the worker may be threaded.
    """
    return [
        "/usr/bin/prlimit",
        f"--as={RLIMIT_AS_BYTES}",
        f"--cpu={RLIMIT_CPU_S}",
        f"--nofile={RLIMIT_NOFILE}",
        f"--core={RLIMIT_CORE}",
        "--",
    ]


def bwrap_argv(*, ro_binds: Sequence[str] = (),
               file_binds: Sequence[tuple] = (),
               tmp_home: str = "/sandbox-home") -> List[str]:
    """Minimal policy: no network, no PID namespace sharing, no capabilities,
    dies with the parent, private tmp and HOME, explicit read-only allowlist.
    The repository is never bound.

    Returns the bwrap prefix only; the caller appends the prlimit-wrapped
    interpreter so the limits land inside the sandbox.
    """
    argv = [
        "bwrap",
        "--unshare-net", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--cap-drop", "ALL", "--die-with-parent", "--new-session",
        "--clearenv",
        "--setenv", "HOME", tmp_home,
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "TMPDIR", "/tmp",
        "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--tmpfs", tmp_home,
        "--ro-bind", "/usr", "/usr",
    ]
    for lib in ("/lib", "/lib64", "/etc/ld.so.cache"):
        if Path(lib).exists():
            argv += ["--ro-bind", lib, lib]
    for b in ro_binds:
        argv += ["--ro-bind", b, b]
    for src, dst in file_binds:
        argv += ["--ro-bind", str(src), str(dst)]
    return argv


def sandboxed(python: str, probe: str, *, ro_binds: Sequence[str] = (),
              file_binds: Sequence[tuple] = ()) -> List[str]:
    """Full argv: bwrap policy, then prlimit, then the interpreter."""
    return (bwrap_argv(ro_binds=ro_binds, file_binds=file_binds)
            + _prlimit_prefix() + [python, "-c", probe])


def _run(argv: List[str], timeout: float = 45.0,
         input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          check=False, input=input_text, shell=False,
                          start_new_session=True)


def minimal_pdf(message: str = "GOLD SMOKE TEXT") -> bytes:
    """A tiny, valid, uncompressed PDF with a real text layer and a correct
    xref table. Authored by hand so no PDF library is needed to create it."""
    content = f"BT /F1 12 Tf 20 100 Td ({message}) Tj ET\n".encode("ascii")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode("ascii") + b">>stream\n"
        + content + b"endstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj".encode("ascii") + body + b"endobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (f"trailer<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n"
            f"{xref_at}\n%%EOF\n").encode("ascii")
    return bytes(out)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
SANDBOX_PYTHON = "/usr/bin/python3"
"""Interpreter used for the generic probes.

Deliberately NOT sys.executable: the repository venv lives under the repo, and
the repo is never bound into the sandbox. Passing the venv interpreter produces
exec failures that look like probe results - which is how the first Stage A run
recorded a false PASS on the address-space limit (rc=127 from a failed exec,
not from the limit biting).
"""


def check_available() -> SmokeCheck:
    missing = [t for t in ("bwrap", "prlimit") if not shutil.which(t)]
    if not missing and not Path(SANDBOX_PYTHON).exists():
        missing.append(SANDBOX_PYTHON)
    return SmokeCheck("tools_present", not missing,
                      f"bwrap+prlimit+{SANDBOX_PYTHON} present" if not missing
                      else f"missing {missing}")


def check_isolation(python: str) -> List[SmokeCheck]:
    """Network unreachable, host HOME absent, repository not bound."""
    # The host HOME path is computed at runtime, never written as a literal:
    # a hardcoded /home/<user>/... in source is exactly what the leak scanner
    # is looking for, and it would also be wrong on any other machine.
    host_home = str(Path.home())
    probe = (
        "import os,socket,sys\n"
        "res={}\n"
        "try:\n"
        "    s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',11434))\n"
        "    res['net']='reachable'\n"
        "except Exception as e:\n"
        "    res['net']=type(e).__name__\n"
        "res['home']=os.environ.get('HOME')\n"
        f"res['host_home_visible']=os.path.isdir({host_home!r})\n"
        f"res['repo_visible']=os.path.isdir({str(REPO_ROOT)!r})\n"
        "print(res)\n"
    )
    try:
        cp = _run(sandboxed(python, probe))
    except subprocess.TimeoutExpired:
        return [SmokeCheck("isolation", False, "probe timed out")]
    if cp.returncode != 0:
        return [SmokeCheck("isolation", False,
                           f"rc={cp.returncode} {cp.stderr.strip()[:120]}")]
    try:
        res = eval(cp.stdout.strip(), {"__builtins__": {}})  # noqa: S307 - fixed dict literal
    except Exception:                                        # noqa: BLE001
        return [SmokeCheck("isolation", False, "probe output unparsed")]
    return [
        SmokeCheck("net_unreachable", res.get("net") != "reachable",
                   f"connect -> {res.get('net')}"),
        SmokeCheck("host_home_absent", res.get("host_home_visible") is False,
                   f"HOME={res.get('home')}"),
        SmokeCheck("repo_not_bound", res.get("repo_visible") is False,
                   "repository is not bound into the sandbox"),
    ]


def check_rlimit_as(python: str) -> SmokeCheck:
    """A child that allocates past RLIMIT_AS must die on the ALLOCATION.

    A bare non-zero exit is not evidence: an exec failure also exits non-zero.
    The probe therefore prints a sentinel on the way in, so a failure before the
    allocation is distinguishable from the limit doing its job.
    """
    probe = ("import sys\n"
             "print('ALIVE', flush=True)\n"
             "try:\n"
             "    b = bytearray(3*1024*1024*1024)\n"
             "    print('ALLOCATED', len(b))\n"
             "except MemoryError:\n"
             "    print('MEMORYERROR')\n")
    try:
        cp = _run(sandboxed(python, probe), timeout=60)
    except subprocess.TimeoutExpired:
        return SmokeCheck("rlimit_as_enforced", False, "probe timed out")
    started = "ALIVE" in cp.stdout
    allocated = "ALLOCATED" in cp.stdout
    if not started:
        return SmokeCheck("rlimit_as_enforced", False,
                          f"child never started (rc={cp.returncode}) - not a "
                          f"limit result: {cp.stderr.strip()[:100]}")
    return SmokeCheck("rlimit_as_enforced", not allocated,
                      "3 GiB allocation refused under a 1 GiB cap"
                      if not allocated else "allocation succeeded past the cap")


def check_process_group_kill(python: str) -> SmokeCheck:
    """A hung child is killed by process group, leaving nothing behind."""
    probe = "import time\nwhile True: time.sleep(1)"
    proc = subprocess.Popen(sandboxed(python, probe), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, shell=False)
    try:
        proc.wait(timeout=4)
        return SmokeCheck("process_group_kill", False, "child exited on its own")
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), 9)
    except OSError as exc:
        return SmokeCheck("process_group_kill", False, f"killpg failed: {exc}")
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return SmokeCheck("process_group_kill", False, "child survived SIGKILL")
    return SmokeCheck("process_group_kill", True, "process group terminated")


def check_antiword(python: str) -> SmokeCheck:
    if not shutil.which("antiword"):
        return SmokeCheck("antiword_sandboxed", False, "antiword not installed")
    probe = ("import subprocess;"
             "cp=subprocess.run(['antiword','-h'],capture_output=True);"
             "print('ran', cp.returncode)")
    try:
        cp = _run(sandboxed(python, probe))
    except subprocess.TimeoutExpired:
        return SmokeCheck("antiword_sandboxed", False, "timed out")
    return SmokeCheck("antiword_sandboxed", "ran" in cp.stdout,
                      cp.stdout.strip()[:80] or cp.stderr.strip()[:80])


def check_pymupdf(scratch_python: str) -> List[SmokeCheck]:
    """Import PyMuPDF inside the sandbox and extract text from one benign PDF.

    This is an import-and-smoke check only. C0B did NOT benchmark PDF
    extraction quality; that is C5.
    """
    if not Path(scratch_python).exists():
        return [SmokeCheck("pymupdf_sandboxed", False,
                           "scratch interpreter absent")]
    # Not resolve(): bin/python is a symlink to the system interpreter, so
    # resolving it would bind /usr instead of the scratch venv.
    venv_root = str(Path(scratch_python).parents[1])
    with tempfile.TemporaryDirectory(prefix="dirracuda-c0b1-pdf-") as td:
        pdf = Path(td) / "smoke.pdf"
        pdf.write_bytes(minimal_pdf())
        probe = (
            "import pymupdf, sys\n"
            "d=pymupdf.open('/input.pdf')\n"
            "t=''.join(p.get_text() for p in d)\n"
            "print('VER', pymupdf.__version__, pymupdf.mupdf_version)\n"
            "print('TEXT', 'GOLD SMOKE TEXT' in t)\n"
        )
        argv = sandboxed(scratch_python, probe, ro_binds=[venv_root],
                         file_binds=[(pdf, "/input.pdf")])
        try:
            cp = _run(argv, timeout=90)
        except subprocess.TimeoutExpired:
            return [SmokeCheck("pymupdf_sandboxed", False, "timed out")]
    if cp.returncode != 0:
        return [SmokeCheck("pymupdf_sandboxed", False,
                           f"rc={cp.returncode} {cp.stderr.strip()[:160]}")]
    ver = next((l for l in cp.stdout.splitlines() if l.startswith("VER")), "")
    txt = "TEXT True" in cp.stdout
    return [
        SmokeCheck("pymupdf_import_sandboxed", bool(ver), ver.strip()),
        SmokeCheck("pymupdf_text_extracted", txt,
                   "benign PDF text layer read inside the sandbox"),
    ]


def run_all(python: Optional[str] = None,
            scratch_python: Optional[str] = None) -> List[SmokeCheck]:
    """`python` defaults to SANDBOX_PYTHON, which is what the sandbox can see."""
    py = python or SANDBOX_PYTHON
    checks = [check_available()]
    if not checks[0].ok:
        return checks
    checks += check_isolation(py)
    checks.append(check_rlimit_as(py))
    checks.append(check_process_group_kill(py))
    checks.append(check_antiword(py))
    if scratch_python:
        checks += check_pymupdf(scratch_python)
    return checks
