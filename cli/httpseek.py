#!/usr/bin/env python3
"""HTTP server discovery and assessment — CLI entry point."""

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.cli_bootstrap import create_common_seek_parser, run_standard_seek_cli
from shared.http_workflow import create_http_workflow
from commands.http.models import HttpDiscoveryError


def create_parser():
    return create_common_seek_parser(
        prog="httpseek",
        description="HTTP server discovery and assessment",
    )


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    return run_standard_seek_cli(args, create_http_workflow, HttpDiscoveryError)


if __name__ == "__main__":
    sys.exit(main())
