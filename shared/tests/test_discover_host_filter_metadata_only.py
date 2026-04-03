import sys
import types
from types import SimpleNamespace


if "shodan" not in sys.modules:
    shodan_stub = types.ModuleType("shodan")
    shodan_stub.Shodan = object
    sys.modules["shodan"] = shodan_stub

from commands.discover import host_filter


def _make_op() -> SimpleNamespace:
    return SimpleNamespace(
        shodan_host_metadata={},
        exclusion_patterns=["example isp", "cloud"],
        output=SimpleNamespace(error=lambda *_args, **_kwargs: None),
    )


def test_should_exclude_ip_uses_metadata_only_and_does_not_require_api_lookup():
    op = _make_op()
    op.shodan_host_metadata["10.20.30.1"] = {
        "org_normalized": "example isp ltd",
        "isp_normalized": "example isp ltd",
    }

    assert host_filter.should_exclude_ip(op, "10.20.30.1") is True


def test_should_exclude_ip_returns_false_when_metadata_missing():
    op = _make_op()
    assert host_filter.should_exclude_ip(op, "10.20.30.2") is False
