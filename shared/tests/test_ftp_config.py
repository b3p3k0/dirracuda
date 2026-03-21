"""
Tests for FTP-specific SMBSeekConfig getters:
  - get_max_concurrent_ftp_discovery_hosts()
  - get_max_concurrent_ftp_access_hosts()
"""
import pytest
from shared.config import SMBSeekConfig


def _config_with_ftp(ftp_section: dict) -> SMBSeekConfig:
    """Return an SMBSeekConfig whose .config dict has the given ftp section injected."""
    cfg = SMBSeekConfig.__new__(SMBSeekConfig)
    cfg.config_file = "test"
    cfg.config = {"ftp": ftp_section}
    return cfg


def _config_without_ftp() -> SMBSeekConfig:
    """Return an SMBSeekConfig with no ftp section at all."""
    cfg = SMBSeekConfig.__new__(SMBSeekConfig)
    cfg.config_file = "test"
    cfg.config = {}
    return cfg


# ---------------------------------------------------------------------------
# get_max_concurrent_ftp_discovery_hosts
# ---------------------------------------------------------------------------

class TestFtpDiscoveryConcurrency:
    def test_returns_valid_configured_value(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": 5}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 5

    def test_default_when_ftp_section_missing(self):
        cfg = _config_without_ftp()
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_discovery_subsection_missing(self):
        cfg = _config_with_ftp({"verification": {"connect_timeout": 5}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_key_missing_in_discovery(self):
        cfg = _config_with_ftp({"discovery": {}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_value_is_zero(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": 0}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_value_is_negative(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": -3}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_value_is_string(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": "10"}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_default_when_value_is_none(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": None}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 10

    def test_accepts_value_of_one(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": 1}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 1

    def test_accepts_large_value(self):
        cfg = _config_with_ftp({"discovery": {"max_concurrent_hosts": 100}})
        assert cfg.get_max_concurrent_ftp_discovery_hosts() == 100


# ---------------------------------------------------------------------------
# get_max_concurrent_ftp_access_hosts
# ---------------------------------------------------------------------------

class TestFtpAccessConcurrency:
    def test_returns_valid_configured_value(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": 2}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 2

    def test_default_when_ftp_section_missing(self):
        cfg = _config_without_ftp()
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_access_subsection_missing(self):
        cfg = _config_with_ftp({"verification": {"auth_timeout": 10}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_key_missing_in_access(self):
        cfg = _config_with_ftp({"access": {}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_value_is_zero(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": 0}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_value_is_negative(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": -1}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_value_is_string(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": "4"}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_default_when_value_is_none(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": None}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 4

    def test_accepts_value_of_one(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": 1}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 1

    def test_accepts_large_value(self):
        cfg = _config_with_ftp({"access": {"max_concurrent_hosts": 50}})
        assert cfg.get_max_concurrent_ftp_access_hosts() == 50
