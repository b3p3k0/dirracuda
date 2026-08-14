"""Fail-closed command surface for the C0B-6 public confirmation.

Private execution is unreachable from this namespace. Imports that can resolve user
paths or construct transport occur only after the explicit command gate.

DISPOSITION: benchmark-only; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from .c0b2_cli import EXIT_BLOCKED, EXIT_USAGE, _add_run_id

BENCHMARK_PROTOCOL_ID = "c0b6-repaired-confirmation-v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.analyst_benchmark c0b6",
        description="C0B-6 assistive review-budget public confirmation.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create", help="create the frozen C0B-6 child run")
    for name in ("status", "verify"):
        command = commands.add_parser(name, help=f"{name} a C0B-6 run")
        _add_run_id(command)
    for name in ("run", "resume"):
        command = commands.add_parser(name, help=f"{name} the C0B-6 run")
        _add_run_id(command)
        command.add_argument("--confirm-live", action="store_true")
    abandon = commands.add_parser("abandon", help="abandon a C0B-6 run")
    _add_run_id(abandon)
    abandon.add_argument("--confirm-abandon", action="store_true")
    leak = commands.add_parser("leak-scan", help="scan the exact C0B-6 task delta")
    leak.add_argument("--baseline-file", type=Path, required=True)
    leak.add_argument("--raw-artifact", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command in {"run", "resume"} and not args.confirm_live:
        print("--confirm-live is required before any C0B-6 live operation.",
              file=sys.stderr)
        return EXIT_USAGE
    if args.command == "abandon" and not args.confirm_abandon:
        print("--confirm-abandon is required.", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.command == "leak-scan":
            scanner = importlib.import_module("scripts.analyst_benchmark.leakscan")
            return scanner.run(
                mode="public", baseline_path=args.baseline_file,
                raw_artifacts=args.raw_artifact, protocol_id=BENCHMARK_PROTOCOL_ID)
        runtime = importlib.import_module("scripts.analyst_benchmark.c0b6_runtime")
        if args.command == "create":
            result = {"run_id": runtime.create_confirmation_run()}
        elif args.command == "status":
            result = runtime.confirmation_status(args.run_id)
        elif args.command == "verify":
            result = runtime.confirmation_verify(args.run_id)
            print(runtime.render_public(result))
            return 0 if result["ok"] else EXIT_BLOCKED
        elif args.command == "abandon":
            result = runtime.abandon_confirmation_run(args.run_id)
        else:
            result = runtime.run_confirmation(
                args.run_id, resume=args.command == "resume")
        print(runtime.render_public(result))
        return 0
    except Exception:  # Never expose paths, response text, or exception details.
        code = f"{args.command.replace('-', '_')}_failed"
        print(f"C0B-6 BLOCKED: {code}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
