"""Data models for FTP scan results."""
from dataclasses import dataclass, field
from typing import List


@dataclass
class FtpScanResult:
    """Summary result returned by FtpWorkflow.run()."""
    country: str
    hosts_scanned: int = 0
    hosts_accessible: int = 0
    errors: List[str] = field(default_factory=list)
    success: bool = True
