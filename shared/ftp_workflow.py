"""
FTP scan workflow orchestrator (Card 4).

Completely separate from shared/workflow.py — no changes to SMB workflow.
"""
from __future__ import annotations

import argparse
import sys


class _FtpOutput:
    """Minimal stdout wrapper with flush=True for subprocess pipe streaming."""

    def __init__(self, verbose: bool = False, no_colors: bool = False) -> None:
        self.verbose = verbose
        self.no_colors = no_colors

    def info(self, msg: str) -> None:
        print(f"ℹ  {msg}", flush=True)

    def success(self, msg: str) -> None:
        print(f"✓  {msg}", flush=True)

    def error(self, msg: str) -> None:
        print(f"✗  {msg}", file=sys.stderr, flush=True)

    def raw(self, msg: str) -> None:
        """Emit verbatim — used for 📊 Progress lines."""
        print(msg, flush=True)

    def workflow_step(self, name: str, num: int, total: int) -> None:
        """[n/m] header — matched by progress.py workflow_step_pattern."""
        print(f"[{num}/{total}] {name}", flush=True)


class FtpWorkflow:
    """FTP scan workflow orchestrator."""

    STEP_COUNT = 2

    def __init__(self, output: _FtpOutput, config, db_path: str) -> None:
        self.output = output
        self.config = config
        self.db_path = db_path
        self.args: argparse.Namespace | None = None

    def run(self, args: argparse.Namespace) -> None:
        from commands.ftp.models import FtpDiscoveryError

        self.args = args
        country = getattr(args, "country", None) or "ALL"
        out = self.output

        out.info(f"FTP scan starting — country filter: {country}")

        out.workflow_step("FTP Discovery", 1, self.STEP_COUNT)
        from commands.ftp.operation import run_discover_stage
        try:
            reachable, shodan_total = run_discover_stage(self)
        except FtpDiscoveryError:
            raise  # re-raise to ftpseek main() for exit(1)

        out.workflow_step("FTP Access Verification", 2, self.STEP_COUNT)
        from commands.ftp.operation import run_access_stage
        accessible = run_access_stage(self, reachable)

        # Rollup lines parsed by progress.py field regexes.
        out.raw(f"📊 Hosts Scanned: {shodan_total}")
        out.raw(f"🔓 Hosts Accessible: {accessible}")
        out.raw(f"📁 Accessible Shares: 0")

        # Success marker — must NOT be emitted on error paths.
        out.raw("🎉 FTP scan completed successfully")


def create_ftp_workflow(args: argparse.Namespace) -> FtpWorkflow:
    """Factory mirroring create_unified_workflow() in shared/workflow.py."""
    from shared.config import load_config

    config = load_config(getattr(args, "config", None))
    db_path = config.get_database_path()
    output = _FtpOutput(
        verbose=getattr(args, "verbose", False),
        no_colors=getattr(args, "no_colors", False),
    )
    return FtpWorkflow(output, config, db_path)
