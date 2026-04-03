import sys
import types


# commands.discover imports shodan at module import time.
if "shodan" not in sys.modules:
    shodan_stub = types.ModuleType("shodan")
    shodan_stub.Shodan = object
    shodan_stub.APIError = Exception
    sys.modules["shodan"] = shodan_stub

from commands.discover.operation import DiscoverOperation
import commands.discover.operation as discover_operation


class _OutputStub:
    def __init__(self) -> None:
        self.info_messages = []
        self.verbose_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, message: str) -> None:
        self.info_messages.append(message)

    def print_if_verbose(self, message: str) -> None:
        self.verbose_messages.append(message)

    def warning(self, message: str) -> None:
        self.warning_messages.append(message)

    def error(self, message: str) -> None:
        self.error_messages.append(message)


class _DatabaseStub:
    def __init__(self) -> None:
        self.filter_input = None

    def get_new_hosts_filter(self, shodan_ips, rescan_all=False, rescan_failed=False, output_manager=None):
        self.filter_input = set(shodan_ips)
        stats = {
            "total_from_shodan": len(shodan_ips),
            "known_hosts": 0,
            "new_hosts": len(shodan_ips),
            "recently_scanned": 0,
            "failed_hosts": 0,
            "to_scan": len(shodan_ips),
        }
        return set(shodan_ips), stats

    def display_scan_statistics(self, stats, ips_to_scan):
        _ = stats, ips_to_scan


class _DiscoverOperationHarness:
    execute = DiscoverOperation.execute

    def __init__(self) -> None:
        self.output = _OutputStub()
        self.database = _DatabaseStub()
        self.shodan_api = object()
        self.shodan_host_metadata = {
            "10.10.10.1": {"country_name": "US", "country_code": "US"},
            "10.10.10.2": {"country_name": "US", "country_code": "US"},
            "10.10.10.3": {"country_name": "US", "country_code": "US"},
        }
        self._auth_method_cache = {}
        self.stats = {
            "shodan_results": 0,
            "excluded_ips": 0,
            "new_hosts": 0,
            "skipped_hosts": 0,
            "successful_auth": 0,
            "failed_auth": 0,
            "total_processed": 0,
        }
        self.apply_exclusions_called = False

    def _query_shodan(self, country=None, custom_filters=None):
        _ = country, custom_filters
        return {"10.10.10.1", "10.10.10.2", "10.10.10.3"}, "country:US"

    def _apply_exclusions(self, ip_addresses):
        self.apply_exclusions_called = True
        raise AssertionError("_apply_exclusions must be skipped in query-only exclusion mode")

    def _test_smb_authentication(self, ip_addresses, country=None):
        _ = country
        assert set(ip_addresses) == {"10.10.10.1", "10.10.10.2", "10.10.10.3"}
        return []

    def _save_to_database(self, successful_hosts, country=None):
        _ = successful_hosts, country
        return set()


def test_execute_skips_local_exclusion_pass(monkeypatch):
    monkeypatch.setattr(discover_operation, "SMB_AVAILABLE", True)
    op = _DiscoverOperationHarness()

    result = op.execute(country="US", rescan_all=False, rescan_failed=False, force_hosts=set(), custom_filters="")

    assert op.apply_exclusions_called is False
    assert op.database.filter_input == {"10.10.10.1", "10.10.10.2", "10.10.10.3"}
    assert result.total_hosts == 3
