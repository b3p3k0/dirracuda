"""
FTP scan operation skeleton (Card 2).

Emits GUI-compatible progress lines; no real FTP I/O.
Real discovery/auth/listing added in Cards 4-5.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.ftp_workflow import FtpWorkflow

_SKELETON_STEPS = 10


def run_discover_stage(workflow: "FtpWorkflow") -> int:
    """Placeholder discovery. Returns 0 candidates (skeleton)."""
    out = workflow.output
    out.info("FTP discovery stage (skeleton — no Shodan query yet)")
    for i in range(1, _SKELETON_STEPS + 1):
        pct = (i / _SKELETON_STEPS) * 100
        out.raw(f"📊 Progress: {i}/{_SKELETON_STEPS} ({pct:.1f}%)")
        time.sleep(0.05)
    out.info("Discovery complete: 0 FTP candidates (skeleton)")
    return 0


def run_access_stage(workflow: "FtpWorkflow", candidate_count: int) -> int:
    """Placeholder access/auth stage. Returns 0 accessible (skeleton)."""
    out = workflow.output
    total = max(candidate_count, _SKELETON_STEPS)
    out.info("FTP access verification stage (skeleton — no real auth yet)")
    for i in range(1, total + 1):
        pct = (i / total) * 100
        out.raw(f"📊 Progress: {i}/{total} ({pct:.1f}%)")
        time.sleep(0.05)
    out.info("Access verification complete: 0 accessible FTP hosts (skeleton)")
    return 0
