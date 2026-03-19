"""Data models for HTTP scan results."""
from dataclasses import dataclass, field
from typing import List


@dataclass
class HttpScanResult:
    """Summary result returned by HttpWorkflow.run()."""
    country: str
    hosts_scanned: int = 0
    hosts_accessible: int = 0
    errors: List[str] = field(default_factory=list)
    success: bool = True


class HttpDiscoveryError(Exception):
    """Raised by Shodan query on API failure. Caught at CLI boundary."""
