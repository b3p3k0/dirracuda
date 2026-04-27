#!/usr/bin/env python3
"""FTP server discovery and assessment — CLI entry point."""

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from shared.ftp_workflow import create_ftp_workflow
from commands.ftp.models import FtpDiscoveryError
from shared.path_service import (
    get_legacy_paths,
    get_paths,
    resolve_runtime_main_db_for_session,
    run_layout_v2_migration,
)
from shared.db_path_resolution import normalize_database_path

_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)


def _prepare_runtime_db_override(config_path: str | None) -> str:
    """Run layout migration preflight and return effective DB path for this run."""
    try:
        migration_result = run_layout_v2_migration(paths=_PATHS, legacy=_LEGACY)
    except Exception as exc:
        print(
            f"Warning: startup layout migration preflight failed: {exc}",
            file=sys.stderr,
        )
        migration_result = {
            "status": "failed",
            "db_recovery_attempted": True,
            "db_recovery_status": "failed",
            "db_fallback_candidates": [],
        }

    try:
        from shared.config import load_config
        cfg = load_config(config_path)
        preferred = cfg.get_database_path()
    except Exception as exc:
        print(
            f"Warning: failed to resolve configured database path; using canonical default: {exc}",
            file=sys.stderr,
        )
        preferred = str(_PATHS.main_db_file)

    preferred_path = normalize_database_path(preferred, _PATHS.repo_root)
    if preferred_path is None:
        preferred_path = _PATHS.main_db_file.resolve(strict=False)
    effective_path, warning = resolve_runtime_main_db_for_session(
        preferred_path,
        migration_result=migration_result,
        paths=_PATHS,
        legacy=_LEGACY,
    )
    if warning:
        print(f"Warning: {warning}", file=sys.stderr)
    return str(effective_path)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ftpseek",
        description="FTP server discovery and assessment",
    )
    parser.add_argument(
        "--country", metavar="CODE", default=None,
        help="ISO 3166-1 alpha-2 country code(s), comma-separated (e.g. US,GB)",
    )
    parser.add_argument(
        "--config", metavar="FILE", default=None,
        help=f"Path to config file (default: {_PATHS.config_file})",
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


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    if args.verbose and args.quiet:
        print("Error: Cannot use both --quiet and --verbose options")
        return 1

    runtime_db_override = _prepare_runtime_db_override(getattr(args, "config", None))
    if runtime_db_override:
        setattr(args, "runtime_db_path_override", runtime_db_override)

    # Ensure FTP (and SMB) schema migrations are applied before any workflow runs.
    # Best-effort: mirrors the pattern in SMBSeekWorkflowDatabase.__init__.
    try:
        from shared.db_migrations import run_migrations
        run_migrations(runtime_db_override)
    except Exception as mig_exc:
        print(
            f"Warning: failed to apply DB migrations before FTP scan: {mig_exc}",
            file=sys.stderr,
        )

    try:
        workflow = create_ftp_workflow(args)
        workflow.run(args)
    except KeyboardInterrupt:
        print("\nScan interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except FtpDiscoveryError as exc:
        print(f"✗  {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
