"""Shared CLI bootstrap helpers for seeker entrypoints."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable


def ensure_repo_root_on_path() -> Path:
    """Add the repository root to sys.path when running CLI scripts directly."""
    repo_root = Path(__file__).resolve().parent.parent
    root_str = str(repo_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return repo_root


def create_common_seek_parser(prog: str, description: str) -> argparse.ArgumentParser:
    """Build the shared argument parser used by ftpseek and httpseek."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
    )
    parser.add_argument(
        "--country", metavar="CODE", default=None,
        help="ISO 3166-1 alpha-2 country code(s), comma-separated (e.g. US,GB)",
    )
    parser.add_argument(
        "--config", metavar="FILE", default=None,
        help="Path to config file (default: conf/config.json)",
    )
    parser.add_argument(
        "--filter",
        metavar="QUERY",
        default="",
        help="Additional Shodan filter query to append (raw syntax).",
    )
    parser.add_argument("--verbose", "-v", action="store_true", default=False)
    parser.add_argument("--quiet", "-q", action="store_true", default=False)
    parser.add_argument("--no-colors", action="store_true", default=False)
    return parser


def run_best_effort_migrations(config_path: str | None) -> None:
    """Apply schema migrations if configuration loading succeeds."""
    try:
        from shared.config import load_config
        from shared.db_migrations import run_migrations

        cfg = load_config(config_path)
        run_migrations(cfg.get_database_path())
    except Exception:
        pass


def run_standard_seek_cli(
    args: argparse.Namespace,
    workflow_factory: Callable[[argparse.Namespace], Any],
    discovery_error_type: type[Exception],
) -> int:
    """Execute the common ftpseek/httpseek command lifecycle."""
    if args.verbose and args.quiet:
        print("Error: Cannot use both --quiet and --verbose options")
        return 1

    run_best_effort_migrations(getattr(args, "config", None))

    try:
        workflow = workflow_factory(args)
        workflow.run(args)
    except KeyboardInterrupt:
        print("\nScan interrupted by user.", file=sys.stderr)
        return 130
    except discovery_error_type as exc:
        print(f"✗  {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        return 1

    return 0
