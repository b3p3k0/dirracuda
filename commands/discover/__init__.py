"""
Dirracuda Discover Package

Provides discovery orchestration plus supporting helpers for Shodan queries,
host filtering, and SMB authentication testing. This package replaces the
single monolithic commands.discover module to improve readability and
maintainability while keeping the public API stable.
"""

from .models import DiscoverResult
from .operation import DiscoverOperation
from .cli import DiscoverCommand

__all__ = ["DiscoverResult", "DiscoverOperation", "DiscoverCommand"]
