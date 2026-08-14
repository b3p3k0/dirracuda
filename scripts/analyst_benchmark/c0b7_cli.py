"""Offline-only command surface for C0B-7 evidence recovery.

DISPOSITION: benchmark-only; retain with the accepted C0B outcome.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.analyst_benchmark c0b7",
        description="Recover the C0B-6 decision without model contact.")
    command = parser.add_subparsers(dest="command", required=True)
    recover = command.add_parser("recover")
    recover.add_argument("--checkpoint", type=Path, required=True)
    recover.add_argument("--snapshot", type=Path, required=True)
    recover.add_argument("--trusted-root", type=Path, required=True)
    leak = command.add_parser("leak-scan")
    leak.add_argument("--baseline-file", type=Path, required=True)
    leak.add_argument("--raw-artifact", type=Path, action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.command == "leak-scan":
            from .leakscan import run
            return run(
                baseline_path=args.baseline_file,
                raw_artifacts=args.raw_artifact, mode="public",
                protocol_id="c0b7-offline-recovery-v1")
        from .c0b7_recovery import recover, render_public
        value = recover(
            args.checkpoint, args.snapshot, trusted_root=args.trusted_root)
    except Exception:
        print("C0B-7 BLOCKED: recovery_failed", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(render_public(value) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
