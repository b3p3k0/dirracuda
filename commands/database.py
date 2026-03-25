#!/usr/bin/env python3
"""
Dirracuda Database Command - DEPRECATED

This command has been deprecated in Dirracuda 3.0.0.
Database operations have been removed from the main CLI.

Use the main Dirracuda command for discovery and share enumeration:
    ./smbseek.py --country US

For database operations, use the tools directly:
    python tools/db_query.py --summary
    python tools/db_maintenance.py --backup
"""

import sys


def main():
    """Main entry point for deprecated database command."""
    print("⚠️  DEPRECATED: database command is no longer supported.")
    print("   Use: ./smbseek.py --country US  (includes discovery + share enumeration)")
    print("   For database operations, use tools directly:")
    print("     python tools/db_query.py --summary")
    print("     python tools/db_maintenance.py --backup")
    return 0


if __name__ == '__main__':
    sys.exit(main())