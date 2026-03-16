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


@dataclass
class FtpCandidate:
    """A single Shodan-discovered FTP host that passed port reachability."""
    ip: str
    port: int           # 21 for Card 4
    banner: str         # Shodan banner field; '' if absent
    country: str        # full country name
    country_code: str   # ISO alpha-2
    shodan_data: dict   # raw Shodan match metadata; serialised to JSON for DB


@dataclass
class FtpDiscoveryOutcome:
    """One entry per port-failed host (stage 1)."""
    ip: str
    country: str
    country_code: str
    port: int
    banner: str         # Shodan banner (FTP connect didn't complete)
    shodan_data: str    # json.dumps(metadata)
    reason: str         # 'connect_fail' or 'timeout'
    error_message: str  # short human-readable description


@dataclass
class FtpAccessOutcome:
    """One entry per reachable host (stage 2)."""
    ip: str
    country: str
    country_code: str
    port: int
    banner: str                  # FTP connect banner
    shodan_data: str             # json.dumps(metadata)
    accessible: bool
    auth_status: str             # from authoritative table in Section 4
    root_listing_available: bool
    root_entry_count: int
    error_message: str
    access_details: str          # json.dumps({"reason": ..., "banner": ...})


class FtpDiscoveryError(Exception):
    """Raised by shodan_query.py on API failure. Caught at CLI boundary."""
