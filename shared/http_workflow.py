"""
HTTP scan workflow orchestrator.

Completely separate from shared/workflow.py and shared/ftp_workflow.py —
no changes to SMB or FTP workflows.
"""
from __future__ import annotations

import argparse


class HttpWorkflow:
    """HTTP scan workflow orchestrator."""

    STEP_COUNT = 2

    def __init__(self, output, config, db_path: str) -> None:
        self.output = output
        self.config = config
        self.db_path = db_path
        self.args: argparse.Namespace | None = None
        self.last_accessible_directory_count = 0

    def run(self, args: argparse.Namespace) -> None:
        from commands.http.models import HttpDiscoveryError

        self.args = args
        country = getattr(args, "country", None) or "ALL"
        out = self.output

        out.info(f"HTTP scan starting — country filter: {country}")

        out.workflow_step("HTTP Discovery", 1, self.STEP_COUNT)
        from commands.http.operation import run_discover_stage
        try:
            candidates, shodan_total = run_discover_stage(self)
        except HttpDiscoveryError:
            raise  # re-raise to httpseek main() for exit(1)

        out.workflow_step("HTTP Access Verification", 2, self.STEP_COUNT)
        from commands.http.operation import run_access_stage
        accessible = run_access_stage(self, candidates)
        directories_found = int(getattr(self, "last_accessible_directory_count", 0))

        # Rollup lines parsed by progress.py field regexes.
        out.raw(f"📊 Hosts Scanned: {shodan_total}")
        out.raw(f"🔓 Hosts Accessible: {accessible}")
        out.raw(f"📁 Accessible Directories: {directories_found}")

        # Success marker — must NOT be emitted on error paths.
        out.raw("🎉 HTTP scan completed successfully")


def create_http_workflow(args: argparse.Namespace) -> HttpWorkflow:
    """Factory mirroring create_ftp_workflow() in shared/ftp_workflow.py."""
    from shared.config import load_config
    from shared.output import create_output_manager

    config = load_config(getattr(args, "config", None))
    db_path = config.get_database_path()
    output = create_output_manager(
        config,
        quiet=getattr(args, "quiet", False),
        verbose=getattr(args, "verbose", False),
        no_colors=getattr(args, "no_colors", False),
    )
    return HttpWorkflow(output, config, db_path)
