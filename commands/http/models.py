"""Data models for HTTP scan results."""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class HttpCandidate:
    """A Shodan-discovered HTTP host that passed the TCP port reachability check."""
    ip: str
    port: int               # from Shodan (80, 443, or other)
    scheme: str             # 'http' or 'https' (inferred: 'https' if port==443, else 'http')
    banner: str             # Shodan data/banner field; '' if absent
    title: str              # Shodan http.title; '' if absent
    country: str            # full country name
    country_code: str       # ISO alpha-2
    shodan_data: dict       # lightweight metadata: {org, isp, country_name, country_code, port, hostnames}


@dataclass
class HttpDiscoveryOutcome:
    """One entry per host that failed TCP reachability in Stage 1."""
    ip: str
    country: str
    country_code: str
    port: int
    scheme: str
    banner: str
    title: str
    shodan_data: str        # json.dumps(metadata dict)
    reason: str             # 'timeout' | 'connect_fail'
    error_message: str


@dataclass
class HttpAccessOutcome:
    """One entry per host that entered Stage 2 (all outcomes, success and failure)."""
    ip: str
    country: str
    country_code: str
    port: int               # winning port (candidate port or canonical 80/443)
    scheme: str             # winning scheme ('http' or 'https')
    banner: str
    title: str              # Shodan title; overridden if live response has better title
    shodan_data: str        # json.dumps(metadata)
    accessible: bool        # True only when is_index_page=True and reason=''
    status_code: int        # 0 on network failure
    is_index_page: bool
    dir_count: int          # root dirs + recursed subdir dirs; 0 when inaccessible
    file_count: int         # root files + recursed subdir files; 0 when inaccessible
    tls_verified: bool      # True when TLS cert verified (requires allow_insecure_tls=False)
    reason: str             # '' on success; taxonomy code on failure
    error_message: str
    access_details: str     # json.dumps({reason, status_code, tls_verified,
                            #  dir_count, file_count,
                            #  attempts: [{scheme, port, status_code, is_index, reason, parse_ok}],
                            #  subdirs: [{path, dir_count, file_count}]})


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
