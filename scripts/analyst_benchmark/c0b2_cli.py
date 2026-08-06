"""Fail-closed C0B-2 command surface for public Stages C, D, and F.

The parser owns confirmation ordering.  Live/path modules are imported only after a
command has passed every applicable gate; every private operation remains held.

DISPOSITION: retained C0B-2 benchmark infrastructure; port or remove on C15.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence

EXIT_USAGE = 2
EXIT_HELD = 3
EXIT_BLOCKED = 4

_OPAQUE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PRIVATE_ACKS = (
    "confirm_live",
    "confirm_private_corpus",
    "confirm_private_authority",
    "confirm_trusted_local_boundary",
)


def _opaque_id(value: str) -> str:
    """Accept an opaque identifier, never a path-like value."""
    if not _OPAQUE_ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "must be 1-128 ASCII letters, digits, dots, underscores, or hyphens")
    return value


def _fd_number(value: str) -> int:
    try:
        fd = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative file descriptor") from exc
    if fd < 0:
        raise argparse.ArgumentTypeError("must be a non-negative file descriptor")
    return fd


def _add_run_id(parser: argparse.ArgumentParser, flag: str = "--run-id") -> None:
    parser.add_argument(flag, required=True, type=_opaque_id)


def _add_private_gate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-private-corpus", action="store_true")
    parser.add_argument("--confirm-private-authority", action="store_true")
    parser.add_argument("--confirm-trusted-local-boundary", action="store_true")
    root = parser.add_mutually_exclusive_group()
    root.add_argument("--private-root-prompt", action="store_true")
    root.add_argument("--private-root-fd", type=_fd_number)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.analyst_benchmark c0b2",
        description="C0B-2 public Stage-C/D/F benchmark (private remains held).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("create", help="create an offline public Stage-C run")
    for name in ("status", "verify"):
        command = commands.add_parser(name, help=f"{name} a public run")
        _add_run_id(command)

    for name in ("run", "resume"):
        command = commands.add_parser(name, help=f"{name} a public run")
        _add_run_id(command)
        command.add_argument("--stage", required=True, choices=("C", "D", "F"))
        command.add_argument("--confirm-live", action="store_true")

    abandon = commands.add_parser("abandon", help="abandon a run (C0B-2A held)")
    _add_run_id(abandon)
    abandon.add_argument("--confirm-abandon", action="store_true")

    for name in ("create-private", "run-private", "resume-private"):
        command = commands.add_parser(name, help=f"{name} (private execution held)")
        _add_run_id(command, "--parent-run" if name == "create-private" else "--run-id")
        _add_private_gate(command)
    return parser


def _private_gate_errors(args: argparse.Namespace) -> list[str]:
    missing = ["--" + name.replace("_", "-") for name in _PRIVATE_ACKS
               if not getattr(args, name)]
    root_modes = int(bool(args.private_root_prompt)) + int(args.private_root_fd is not None)
    if root_modes != 1:
        missing.append("exactly one of --private-root-prompt/--private-root-fd")
    return missing


def _held(command: str) -> int:
    print(f"C0B-2 LIVE HELD: {command} is syntax-only pending live-card authorization.",
          file=sys.stderr)
    return EXIT_HELD


def main(argv: Sequence[str] | None = None) -> int:
    """Run one gated C0B-2 command."""
    args_in = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(args_in)

    if args.command in {"run", "resume"} and not args.confirm_live:
        print("--confirm-live is required before any public live operation.",
              file=sys.stderr)
        return EXIT_USAGE

    if args.command == "abandon" and not args.confirm_abandon:
        print("--confirm-abandon is required.", file=sys.stderr)
        return EXIT_USAGE

    if args.command in {"create-private", "run-private", "resume-private"}:
        errors = _private_gate_errors(args)
        if errors:
            print("private command refused; missing/invalid gate: " + ", ".join(errors),
                  file=sys.stderr)
            return EXIT_USAGE
        # C0B-2A stops here by design.  In particular, do not prompt for or
        # inspect the root fd after merely validating the selected input mode.
        return _held(args.command)

    if args.command == "abandon":
        return _held(args.command)

    # Deliberately local: incomplete/held commands above cannot resolve paths, create
    # checkpoints, or import the HTTP adapter.
    from . import c0b2_runtime as runtime

    try:
        if args.command == "create":
            print(runtime.render_public({"run_id": runtime.create_public_run()}))
            return 0
        if args.command == "status":
            print(runtime.render_public(runtime.public_status(args.run_id)))
            return 0
        if args.command == "verify":
            result = runtime.public_verify(args.run_id)
            print(runtime.render_public(result))
            return 0 if result["ok"] else EXIT_BLOCKED
        if args.command in {"run", "resume"}:
            if args.stage == "C":
                result = runtime.run_public_stage_c(
                    args.run_id, resume=args.command == "resume")
            elif args.stage == "D":
                from .c0b2_runtime_d import run_public_stage_d
                result = run_public_stage_d(
                    args.run_id, resume=args.command == "resume")
            else:
                from .c0b2_runtime_f import run_public_stage_f
                result = run_public_stage_f(
                    args.run_id, resume=args.command == "resume")
            print(runtime.render_public(result))
            return 0
    except Exception as exc:  # bounded enums/types only; transport never exposes raw data
        print(f"C0B-2 BLOCKED: {type(exc).__name__}: {str(exc)[:240]}", file=sys.stderr)
        return EXIT_BLOCKED
    raise AssertionError("unreachable C0B-2 command")


if __name__ == "__main__":
    raise SystemExit(main())
