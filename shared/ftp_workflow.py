"""
FTP scan workflow orchestrator.

Completely separate from shared/workflow.py — no changes to SMB workflow.
"""
from __future__ import annotations

import argparse


class FtpWorkflow:
    """FTP scan workflow orchestrator."""

    STEP_COUNT = 2

    def __init__(self, output, config, db_path: str) -> None:
        self.output = output
        self.config = config
        self.db_path = db_path
        self.args: argparse.Namespace | None = None
        self.last_accessible_directory_count = 0

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
        directories_found = int(getattr(self, "last_accessible_directory_count", 0))

        # Rollup lines parsed by progress.py field regexes.
        out.raw(f"📊 Hosts Scanned: {shodan_total}")
        out.raw(f"🔓 Hosts Accessible: {accessible}")
        out.raw(f"📁 Accessible Directories: {directories_found}")

        # Success marker — must NOT be emitted on error paths.
        out.raw("🎉 FTP scan completed successfully")


def create_ftp_workflow(args: argparse.Namespace) -> FtpWorkflow:
    """Factory mirroring create_unified_workflow() in shared/workflow.py."""
    from shared.config import load_config
    from shared.output import create_output_manager

    config = load_config(getattr(args, "config", None))
    runtime_db_override = str(getattr(args, "runtime_db_path_override", "") or "").strip()
    if runtime_db_override:
        if not isinstance(config.config.get("database"), dict):
            config.config["database"] = {}
        config.config["database"]["path"] = runtime_db_override
    db_path = config.get_database_path()
    output = create_output_manager(
        config,
        quiet=getattr(args, "quiet", False),
        verbose=getattr(args, "verbose", False),
        no_colors=getattr(args, "no_colors", False),
    )
    return FtpWorkflow(output, config, db_path)
