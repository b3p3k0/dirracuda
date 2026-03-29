from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

_VALID_BACKENDS = ("auto", "clamdscan", "clamscan")
_INFECTED_RE = re.compile(r"^.+:\s+(.+)\s+FOUND$", re.MULTILINE)


@dataclass
class ScanResult:
    verdict: str                 # "clean" | "infected" | "error"
    backend_used: Optional[str]  # "clamdscan" | "clamscan" | None
    signature: Optional[str]     # virus name when infected; None otherwise
    exit_code: Optional[int]     # raw process exit code; None on launch failure
    raw_output: str              # combined stdout+stderr
    error: Optional[str]         # human-readable reason when verdict=="error"


class ClamAVScanner:
    def __init__(
        self,
        backend: str = "auto",
        clamscan_path: str = "clamscan",
        clamdscan_path: str = "clamdscan",
        timeout_seconds: int = 60,
    ) -> None:
        self.backend = backend
        self.clamscan_path = clamscan_path
        self.clamdscan_path = clamdscan_path
        self.timeout_seconds = timeout_seconds

    def scan_file(self, path: Union[str, Path]) -> ScanResult:
        if self.backend not in _VALID_BACKENDS:
            return ScanResult(
                verdict="error",
                backend_used=None,
                signature=None,
                exit_code=None,
                raw_output="",
                error=f"invalid backend: {self.backend}",
            )

        resolved = self._resolve_binary()
        if resolved is None:
            return ScanResult(
                verdict="error",
                backend_used=None,
                signature=None,
                exit_code=None,
                raw_output="",
                error="no scanner binary found",
            )

        binary_path, backend_name = resolved
        return self._invoke(binary_path, backend_name, str(path))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_binary(self) -> Optional[Tuple[str, str]]:
        if self.backend == "auto":
            found = shutil.which(self.clamdscan_path)
            if found:
                return (found, "clamdscan")
            found = shutil.which(self.clamscan_path)
            if found:
                return (found, "clamscan")
            return None

        if self.backend == "clamdscan":
            found = shutil.which(self.clamdscan_path)
            if found:
                return (found, "clamdscan")
            return None

        # backend == "clamscan"
        found = shutil.which(self.clamscan_path)
        if found:
            return (found, "clamscan")
        return None

    def _invoke(self, binary_path: str, backend_name: str, path: str) -> ScanResult:
        try:
            proc = subprocess.Popen(
                [binary_path, "--no-summary", path],
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
        except FileNotFoundError:
            return ScanResult(
                verdict="error",
                backend_used=None,
                signature=None,
                exit_code=None,
                raw_output="",
                error=f"scanner not found: {binary_path}",
            )
        except OSError as exc:
            return ScanResult(
                verdict="error",
                backend_used=None,
                signature=None,
                exit_code=None,
                raw_output="",
                error=f"failed to launch scanner: {exc}",
            )

        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return ScanResult(
                verdict="error",
                backend_used=backend_name,
                signature=None,
                exit_code=None,
                raw_output="",
                error=f"scanner timeout: {self.timeout_seconds}s",
            )
        except Exception as exc:  # noqa: BLE001
            return ScanResult(
                verdict="error",
                backend_used=backend_name,
                signature=None,
                exit_code=None,
                raw_output="",
                error=f"unexpected: {exc}",
            )

        combined = stdout + "\n" + stderr
        return self._parse_output(combined, proc.returncode, backend_name)

    def _parse_output(
        self, combined_output: str, exit_code: int, backend_name: str
    ) -> ScanResult:
        raw = combined_output

        if exit_code == 0:
            return ScanResult(
                verdict="clean",
                backend_used=backend_name,
                signature=None,
                exit_code=0,
                raw_output=raw,
                error=None,
            )

        if exit_code == 1:
            match = _INFECTED_RE.search(combined_output)
            signature = match.group(1) if match else None
            return ScanResult(
                verdict="infected",
                backend_used=backend_name,
                signature=signature,
                exit_code=1,
                raw_output=raw,
                error=None,
            )

        # exit_code == 2 or anything else
        return ScanResult(
            verdict="error",
            backend_used=backend_name,
            signature=None,
            exit_code=exit_code,
            raw_output=raw,
            error=f"scanner error (exit {exit_code})",
        )


def scanner_from_config(cfg: dict) -> ClamAVScanner:
    """Build a ClamAVScanner from the 'clamav' config section dict."""
    return ClamAVScanner(
        backend=cfg.get("backend", "auto"),
        clamscan_path=cfg.get("clamscan_path", "clamscan"),
        clamdscan_path=cfg.get("clamdscan_path", "clamdscan"),
        timeout_seconds=int(cfg.get("timeout_seconds", 60)),
    )
