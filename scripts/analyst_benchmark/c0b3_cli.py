"""Fail-closed C0B-3 public command surface; private execution remains held.

DISPOSITION: benchmark-only diagnostic; remove after accepted C0B artifacts.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence
from pathlib import Path

from .c0b2_cli import EXIT_BLOCKED, EXIT_USAGE, _add_run_id
from .c0b3_policy import BENCHMARK_PROTOCOL_ID


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.analyst_benchmark c0b3",
        description="C0B-3 assistive-confirmation public benchmark.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create", help="create an offline C0B-3 Stage-C run")
    for name in ("status", "verify"):
        command = commands.add_parser(name, help=f"{name} a recognized public run")
        _add_run_id(command)
    for name in ("run", "resume"):
        command = commands.add_parser(name, help=f"{name} a C0B-3 public run")
        _add_run_id(command)
        command.add_argument("--stage", required=True, choices=("C", "D", "F"))
        command.add_argument("--confirm-live", action="store_true")
    abandon = commands.add_parser("abandon", help="abandon a C0B-3 public run")
    _add_run_id(abandon)
    abandon.add_argument("--confirm-abandon", action="store_true")
    leak = commands.add_parser("leak-scan", help="scan the exact C0B-3 public delta")
    leak.add_argument("--baseline-file", type=Path, required=True)
    leak.add_argument("--raw-artifact", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command in {"run", "resume"} and not args.confirm_live:
        print("--confirm-live is required before any public live operation.",
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
        runtime = importlib.import_module(
            "scripts.analyst_benchmark.c0b2_runtime")

        if args.command == "create":
            result = {"run_id": runtime.create_public_run(
                protocol_id=BENCHMARK_PROTOCOL_ID)}
        elif args.command == "status":
            result = runtime.public_status(args.run_id)
        elif args.command == "verify":
            result = runtime.public_verify(args.run_id)
            print(runtime.render_public(result))
            return 0 if result["ok"] else EXIT_BLOCKED
        elif args.command == "abandon":
            abandon_public_run = importlib.import_module(
                "scripts.analyst_benchmark.c0b2_runtime_common"
            ).abandon_public_run
            result = abandon_public_run(
                args.run_id, expected_protocol_id=BENCHMARK_PROTOCOL_ID)
        elif args.stage == "C":
            result = runtime.run_public_stage_c(
                args.run_id, resume=args.command == "resume",
                expected_protocol_id=BENCHMARK_PROTOCOL_ID)
        elif args.stage == "D":
            run_public_stage_d = importlib.import_module(
                "scripts.analyst_benchmark.c0b2_runtime_d"
            ).run_public_stage_d
            result = run_public_stage_d(
                args.run_id, resume=args.command == "resume",
                expected_protocol_id=BENCHMARK_PROTOCOL_ID)
        else:
            run_public_stage_f = importlib.import_module(
                "scripts.analyst_benchmark.c0b2_runtime_f"
            ).run_public_stage_f
            result = run_public_stage_f(
                args.run_id, resume=args.command == "resume",
                expected_protocol_id=BENCHMARK_PROTOCOL_ID)
        print(runtime.render_public(result))
        return 0
    except Exception:
        print("C0B-3 BLOCKED: operation_failed", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
