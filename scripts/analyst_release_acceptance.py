#!/usr/bin/env python3
"""Run one confirmed public-synthetic Analyst production pipeline acceptance."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experimental.analyst.report_browser import (
    CompletedReportHandle,
    open_completed_report,
)
from experimental.analyst.service import (
    DirectoryRunRequest,
    completed_report_html,
    create_directory_run,
)
from experimental.analyst.worker import WorkerRunResult, run_worker
from experimental.analyst.worker_contract import WorkerOutcome


PROTOCOL_VERSION: Final = "analyst-release-live-v1"
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_RUN_ID: Final = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_CONFIRM_ARG: Final = "--confirm-live"
_PUBLIC_SOURCE: Final = (
    "PUBLIC SYNTHETIC ANALYST RELEASE RECORD. Reserved test data only.\n"
    "This fictional record is not associated with any person or organization.\n"
    "Review contact: analyst-release@example.invalid\n"
    "Documentation address: 192.0.2.88\n"
    "The record exists only to exercise deterministic detection, local structured "
    "review, grounded evidence, and report publication.\n"
)


class ReleaseAcceptanceError(RuntimeError):
    """A content-free release stage failed."""


@dataclass(frozen=True, slots=True)
class ReleaseEvidence:
    """Sanitized terminal evidence for one confirmed public run."""

    outcome: str
    run_id: str
    report_manifest_sha256: str | None
    discovered_files: int | None
    excluded_paths: int | None
    detector_scanned_files: int | None
    selected_files: int | None
    model_reviewed_files: int | None
    detector_hits: int | None
    model_findings: int | None

    def __post_init__(self) -> None:
        if type(self.outcome) is not str or self.outcome not in {
            "complete", "inconclusive_resource",
        }:
            raise ValueError("release evidence outcome is invalid")
        if type(self.run_id) is not str or _RUN_ID.fullmatch(self.run_id) is None:
            raise ValueError("release evidence run identity is invalid")
        counts = (
            self.discovered_files,
            self.excluded_paths,
            self.detector_scanned_files,
            self.selected_files,
            self.model_reviewed_files,
            self.detector_hits,
            self.model_findings,
        )
        if self.outcome == "inconclusive_resource":
            if self.report_manifest_sha256 is not None or any(
                value is not None for value in counts
            ):
                raise ValueError("resource evidence cannot claim a report")
            return
        if (
            type(self.report_manifest_sha256) is not str
            or _SHA256.fullmatch(self.report_manifest_sha256) is None
            or any(type(value) is not int or value < 0 for value in counts)
            or self.discovered_files != 1
            or self.excluded_paths != 0
            or self.detector_scanned_files != 1
            or self.selected_files != 1
            or self.model_reviewed_files != 1
            or self.detector_hits is None
            or self.detector_hits < 1
        ):
            raise ValueError("complete release evidence is contradictory")

    def as_json(self) -> str:
        return json.dumps(
            {
                "protocol": PROTOCOL_VERSION,
                "outcome": self.outcome,
                "run_id": self.run_id,
                "report_manifest_sha256": self.report_manifest_sha256,
                "discovered_files": self.discovered_files,
                "excluded_paths": self.excluded_paths,
                "detector_scanned_files": self.detector_scanned_files,
                "selected_files": self.selected_files,
                "model_reviewed_files": self.model_reviewed_files,
                "detector_hits": self.detector_hits,
                "model_findings": self.model_findings,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def run_release_acceptance(*, confirm_live: bool) -> ReleaseEvidence:
    """Execute exactly one temporary public production run after confirmation."""
    if type(confirm_live) is not bool or not confirm_live:
        raise ReleaseAcceptanceError("live_confirmation_required")
    with tempfile.TemporaryDirectory(prefix="dirracuda-analyst-public-") as raw_root:
        root = Path(raw_root)
        source_root = root / "source"
        output_base = root / "output"
        state_root = root / "state"
        for directory in (source_root, output_base, state_root):
            directory.mkdir(mode=0o700)
        source_file = source_root / "public-release-record.txt"
        source_file.write_text(_PUBLIC_SOURCE, encoding="utf-8")
        os.chmod(source_file, 0o600)
        database = state_root / "analyst.db"
        request = DirectoryRunRequest(
            source_root=source_root.resolve(),
            output_base=output_base.resolve(),
            report_label="Public synthetic release acceptance",
            mode="fast",
        )
        try:
            run_id, _inventory = create_directory_run(request, path=database)
            result = run_worker(run_id, threading.Event(), path=database)
        except Exception:
            raise ReleaseAcceptanceError("pipeline_failed") from None
        if type(result) is not WorkerRunResult:
            raise ReleaseAcceptanceError("pipeline_failed")
        if result.outcome is WorkerOutcome.PAUSED_RESOURCE:
            return ReleaseEvidence(
                "inconclusive_resource", run_id, None,
                None, None, None, None, None, None, None,
            )
        if result.outcome is not WorkerOutcome.COMPLETE:
            raise ReleaseAcceptanceError("pipeline_failed")
        try:
            report = open_completed_report(run_id, path=database)
            html_file = completed_report_html(run_id, path=database)
            html_stat = html_file.lstat()
        except Exception:
            raise ReleaseAcceptanceError("report_verification_failed") from None
        if (
            type(report) is not CompletedReportHandle
            or not stat.S_ISREG(html_stat.st_mode)
            or html_stat.st_size <= 0
        ):
            raise ReleaseAcceptanceError("report_verification_failed")
        return ReleaseEvidence(
            "complete",
            report.run_id,
            report.manifest_sha256,
            report.discovered_files,
            report.excluded_paths,
            report.detector_scanned_files,
            report.selected_files,
            report.model_reviewed_files,
            report.detector_hits,
            report.model_findings,
        )


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values != [_CONFIRM_ARG]:
        print("refusing release acceptance without exact --confirm-live", file=sys.stderr)
        return 2
    try:
        evidence = run_release_acceptance(confirm_live=True)
    except ReleaseAcceptanceError:
        print("Analyst release acceptance FAIL", file=sys.stderr)
        return 4
    except Exception:
        print("Analyst release acceptance FAIL", file=sys.stderr)
        return 4
    print(evidence.as_json())
    return 0 if evidence.outcome == "complete" else 3


if __name__ == "__main__":
    raise SystemExit(main())
