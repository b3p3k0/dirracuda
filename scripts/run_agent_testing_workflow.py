#!/usr/bin/env python3
"""Run Agent Testing Workflow lanes for server-ops scenarios."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_checked(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)


def _run_pytest(marker_expr: str) -> None:
    _run_checked(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            marker_expr,
            "gui/tests",
            "-q",
        ]
    )


def _run_gui_smoke(timeout_seconds: int = 15) -> None:
    cmd = ["xvfb-run", "-a", sys.executable, "./dirracuda", "--mock"]
    print("+", " ".join(cmd), flush=True)
    try:
        subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            check=True,
            timeout=timeout_seconds,
        )
        # Exited quickly with status 0; still acceptable as a smoke pass.
        print(
            "GUI smoke PASS: app launched and exited cleanly before timeout.",
            flush=True,
        )
    except subprocess.TimeoutExpired:
        # Expected: GUI stays open until timeout.
        print(
            f"GUI smoke PASS: app launched and remained running for {timeout_seconds}s.",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Agent Testing Workflow lanes.")
    parser.add_argument(
        "--lane",
        choices=("quick", "deep", "ci"),
        required=True,
        help="Test lane to run.",
    )
    parser.add_argument(
        "--gui-smoke",
        action="store_true",
        help="Run optional GUI smoke check (recommended with --lane deep).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.lane in ("quick", "ci"):
        _run_pytest("scenario or fuzz")
    elif args.lane == "deep":
        _run_pytest("scenario or fuzz or fuzz_heavy")

    if args.gui_smoke:
        _run_gui_smoke(timeout_seconds=15)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
