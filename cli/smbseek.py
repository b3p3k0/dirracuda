#!/usr/bin/env python3
"""
Dirracuda - Unified Security Toolkit

A defensive security toolkit for identifying and analyzing SMB servers
with weak authentication. Simplified single-command interface for
discovery and share enumeration.

Usage:
    smbseek --country US                        # Discovery + share enumeration
    smbseek --country US --verbose              # Same with detailed output
    smbseek --help                              # Help system

Author: Human-AI Collaboration
Version: 3.0.0
"""

import argparse
import sys
import os
import ipaddress
import re
from pathlib import Path
from typing import Optional, Set
from typing import List

# Add repo root to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils import format_string_for_shodan
from shared.path_service import (
    get_legacy_paths,
    get_paths,
    resolve_runtime_main_db_for_session,
    run_layout_v2_migration,
)
from shared.db_path_resolution import normalize_database_path

_PATHS = get_paths()
_LEGACY = get_legacy_paths(paths=_PATHS)


def _prepare_runtime_db_override(config_path: Optional[str]) -> str:
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


def validate_force_hosts(value):
    """
    Validate and parse force hosts argument.

    Args:
        value: Comma-separated IP addresses string

    Returns:
        Set of validated IP address strings

    Raises:
        argparse.ArgumentTypeError: If any IP is invalid
    """
    if not value.strip():
        raise argparse.ArgumentTypeError("forced hosts cannot be empty")

    ips = set()
    for ip_str in value.split(','):
        ip_str = ip_str.strip()
        if not ip_str:
            continue
        try:
            ipaddress.ip_address(ip_str)
            ips.add(ip_str)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{ip_str}' is not a valid IP address (hostnames not supported)")

    if not ips:
        raise argparse.ArgumentTypeError("no valid IP addresses provided")

    return ips


def validate_country_codes(value: str) -> str:
    """
    Validate and normalize country codes.

    Args:
        value: Comma-separated country codes string

    Returns:
        Normalized comma-separated country codes (uppercase)

    Raises:
        argparse.ArgumentTypeError: If any country code is invalid
    """
    if not value or not value.strip():
        raise argparse.ArgumentTypeError("country codes cannot be empty")

    # Common ISO 3166-1 alpha-2 country codes
    valid_codes: Set[str] = {
        'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT',
        'AU', 'AW', 'AX', 'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI',
        'BJ', 'BL', 'BM', 'BN', 'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY',
        'BZ', 'CA', 'CC', 'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN',
        'CO', 'CR', 'CU', 'CV', 'CW', 'CX', 'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM',
        'DO', 'DZ', 'EC', 'EE', 'EG', 'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK',
        'FM', 'FO', 'FR', 'GA', 'GB', 'GD', 'GE', 'GF', 'GG', 'GH', 'GI', 'GL',
        'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT', 'GU', 'GW', 'GY', 'HK', 'HM',
        'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM', 'IN', 'IO', 'IQ', 'IR',
        'IS', 'IT', 'JE', 'JM', 'JO', 'JP', 'KE', 'KG', 'KH', 'KI', 'KM', 'KN',
        'KP', 'KR', 'KW', 'KY', 'KZ', 'LA', 'LB', 'LC', 'LI', 'LK', 'LR', 'LS',
        'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH', 'MK',
        'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW',
        'MX', 'MY', 'MZ', 'NA', 'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP',
        'NR', 'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM',
        'PN', 'PR', 'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW',
        'SA', 'SB', 'SC', 'SD', 'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM',
        'SN', 'SO', 'SR', 'SS', 'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF',
        'TG', 'TH', 'TJ', 'TK', 'TL', 'TM', 'TN', 'TO', 'TR', 'TT', 'TV', 'TW',
        'TZ', 'UA', 'UG', 'UM', 'US', 'UY', 'UZ', 'VA', 'VC', 'VE', 'VG', 'VI',
        'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA', 'ZM', 'ZW'
    }

    # Parse and validate each country code
    codes = []
    for code in value.split(','):
        code = code.strip().upper()
        if not code:
            continue

        if code not in valid_codes:
            raise argparse.ArgumentTypeError(
                f"'{code}' is not a valid ISO 3166-1 alpha-2 country code. "
                f"Examples of valid codes: US, GB, DE, FR, JP, CN, IN, BR"
            )
        codes.append(code)

    if not codes:
        raise argparse.ArgumentTypeError("no valid country codes provided")

    return ','.join(codes)


def validate_custom_filters(filters: Optional[str]) -> str:
    """
    Validate custom Shodan filters from CLI argument.

    Args:
        filters: Raw filter string

    Returns:
        Validated filter string (stripped)

    Raises:
        ValueError: If filters are invalid
    """
    if not filters:
        return ""

    normalized = filters.strip()

    # Basic sanity checks
    if len(normalized) > 500:
        raise ValueError("Custom filters cannot exceed 500 characters")

    # Allow most characters - Shodan syntax is flexible
    # Just prevent obvious injection attempts
    if '\x00' in normalized or '\r' in normalized:
        raise ValueError("Custom filters contain invalid control characters")

    return normalized


def detect_deprecated_usage(argv):
    """Detect and handle deprecated subcommand usage"""
    deprecated_subcommands = {'run', 'discover', 'access', 'collect', 'analyze', 'report', 'db'}
    deprecated_flags = {'--download', '--max-files', '--rescan-all', '--rescan-failed', '--recent', '--pause-between-steps'}

    warnings_issued = []

    # Check for deprecated subcommands
    if len(argv) > 1 and argv[1] in deprecated_subcommands:
        if argv[1] in {'collect', 'analyze', 'report', 'db'}:
            print(f"⚠️  DEPRECATED: '{argv[1]}' subcommand is no longer supported.")
            print("   Use: ./smbseek.py --country US")
            return None  # Exit early with code 1
        else:
            warnings_issued.append(f"subcommand '{argv[1]}' is deprecated, use flags directly")
            argv.pop(1)  # Remove subcommand, continue with flag parsing

    # Check for deprecated flags
    for flag in deprecated_flags:
        if flag in argv:
            warnings_issued.append(f"flag '{flag}' is deprecated and will be ignored")
            argv.remove(flag)

    if warnings_issued:
        for warning in warnings_issued:
            print(f"⚠️  DEPRECATED: {warning}")
        print()

    return argv


def create_main_parser() -> argparse.ArgumentParser:
    """
    Create the main argument parser for unified interface.

    Returns:
        Configured ArgumentParser instance
    """
    parser = argparse.ArgumentParser(
        prog='smbseek',
        description='SMBSeek - Unified SMB Security Toolkit',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  smbseek --country US                        # Complete scan (discovery + share enumeration)
  smbseek --country US --verbose              # Same with detailed output
  smbseek --string Documents                  # Search for banners containing "Documents"
  smbseek --country US --string "Finance"     # Combine string filters with country targeting
  smbseek --help                              # Show help

The tool performs two main operations:
  1. Discovery: Query Shodan and test SMB authentication
  2. Share Access: Enumerate accessible shares on authenticated hosts

Results are automatically saved to the configured database
(default: ~/.dirracuda/data/dirracuda.db).

Documentation: docs/USER_GUIDE.md
"""
    )

    # Security defaults: enable cautious mode unless explicitly disabled
    parser.set_defaults(cautious=True)

    # Required arguments
    parser.add_argument(
        '--country',
        type=str,
        metavar='CODE',
        help='Country code for Shodan search (US, GB, CA, etc.). If not specified, performs global scan with no country filter.'
    )

    # Global options
    parser.add_argument(
        '--config',
        type=str,
        metavar='FILE',
        help=f'Configuration file path (default: {_PATHS.config_file})'
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress output to screen'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--no-colors',
        action='store_true',
        help='Disable colored output'
    )
    parser.add_argument(
        '--force-hosts',
        type=validate_force_hosts,
        action='append',
        metavar='IPS',
        help='Force scanning of specific hosts (comma-separated IPs) even if recently processed or previously failed'
    )
    parser.add_argument(
        '--cautious',
        dest='cautious',
        action='store_true',
        default=None,
        help='Enable modern security hardening (signed SMB sessions, SMB2+/3 only). This is now the default.'
    )
    parser.add_argument(
        '--legacy',
        dest='legacy_mode',
        action='store_true',
        help='Disable cautious mode and allow unsigned/SMB1 connections (not recommended).'
    )
    parser.add_argument(
        '--filter',
        dest='filters',
        type=str,
        metavar='FILTERS',
        help='Custom Shodan filters to append to base query (e.g., "port:445 os:Windows")'
    )
    parser.add_argument(
        '--check-rce',
        action='store_true',
        help='Enable RCE vulnerability analysis during share access testing (opt-in)'
    )
    parser.add_argument(
        '--version',
        action='version',
        version='SMBSeek 3.0.0'
    )

    return parser


def main():
    """Main entry point for SMBSeek unified CLI."""
    # Modify argv before argparse sees it to handle deprecated usage
    cleaned_argv = detect_deprecated_usage(sys.argv[:])
    if cleaned_argv is None:
        return 1  # Exit with failure for unsupported subcommands

    parser = create_main_parser()

    # Parse arguments
    args = parser.parse_args(cleaned_argv[1:])  # Parse modified argv

    # Default to cautious mode when flag not provided
    if getattr(args, 'cautious', None) is None:
        args.cautious = True

    # Allow operators to explicitly opt out via --legacy
    if getattr(args, 'legacy_mode', False):
        args.cautious = False
        print("⚠️  LEGACY MODE: unsigned SMB sessions and SMB1 are permitted. Proceed with extreme caution.")

    # Clean up helper attribute to avoid accidental downstream use
    if hasattr(args, 'legacy_mode'):
        delattr(args, 'legacy_mode')

    # Validate global argument combinations
    if args.quiet and args.verbose:
        print("Error: Cannot use both --quiet and --verbose options")
        return 1

    # Process force_hosts argument (combine multiple uses into single set)
    if hasattr(args, 'force_hosts') and args.force_hosts:
        force_hosts_combined = set()
        for host_set in args.force_hosts:
            force_hosts_combined.update(host_set)
        args.force_hosts = force_hosts_combined
    else:
        args.force_hosts = set()

    runtime_db_override = _prepare_runtime_db_override(getattr(args, "config", None))
    if runtime_db_override:
        setattr(args, "runtime_db_path_override", runtime_db_override)

    try:
        # Import workflow components
        from shared.workflow import create_unified_workflow

        # Validate custom filters before workflow creation
        try:
            args.filters = validate_custom_filters(getattr(args, 'filters', None))
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1

        # Create and execute unified workflow
        workflow = create_unified_workflow(args)
        summary = workflow.run(args)

        # Import output manager for summary display
        from shared.config import load_config
        from shared.output import create_output_manager

        config = load_config(args.config)
        override_path = str(getattr(args, "runtime_db_path_override", "") or "").strip()
        if override_path:
            if not isinstance(config.config.get("database"), dict):
                config.config["database"] = {}
            config.config["database"]["path"] = override_path
        output = create_output_manager(
            config,
            quiet=args.quiet,
            verbose=args.verbose,
            no_colors=args.no_colors
        )

        # Display rollup summary
        output.print_rollup_summary(summary)

        return 0

    except KeyboardInterrupt:
        print("\n\nOperation interrupted by user")
        return 130
    except Exception as e:
        print(f"Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
