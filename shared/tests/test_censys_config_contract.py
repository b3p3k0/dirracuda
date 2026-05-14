from __future__ import annotations

import json
import pytest
from pathlib import Path

from shared.config import SMBSeekConfig


def _make_cfg(tmp_path, censys_section=None, extra=None):
    data: dict = {"shodan": {"api_key": "test-key"}}
    if censys_section is not None:
        data["censys"] = censys_section
    if extra:
        data.update(extra)
    cfg_file = tmp_path / "config.json"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(data), encoding="utf-8")
    return SMBSeekConfig(config_file=str(cfg_file))


# --- PAT ---


def test_pat_empty_string_raises(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"personal_access_token": ""})
    with pytest.raises(ValueError):
        cfg.get_censys_pat()


def test_pat_whitespace_raises(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"personal_access_token": "   "})
    with pytest.raises(ValueError):
        cfg.get_censys_pat()


def test_pat_absent_raises(tmp_path) -> None:
    cfg = _make_cfg(tmp_path)
    with pytest.raises(ValueError):
        cfg.get_censys_pat()


def test_pat_error_message_does_not_contain_raw_value(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"personal_access_token": ""})
    with pytest.raises(ValueError) as exc_info:
        cfg.get_censys_pat()
    msg = str(exc_info.value)
    assert "censys.personal_access_token" in msg
    assert not any(c in msg for c in ("Bearer", "eyJ", "sk-", "token="))


def test_pat_valid_returns_value(tmp_path) -> None:
    secret = "my-valid-pat"
    cfg = _make_cfg(tmp_path, censys_section={"personal_access_token": secret})
    assert cfg.get_censys_pat() == secret


# --- org_id ---


def test_org_id_absent_returns_none(tmp_path) -> None:
    cfg = _make_cfg(tmp_path)
    assert cfg.get_censys_org_id() is None


def test_org_id_empty_string_returns_none(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"organization_id": ""})
    assert cfg.get_censys_org_id() is None


def test_org_id_valid_uuid_accepted(tmp_path) -> None:
    uid = "12345678-1234-1234-1234-123456789abc"
    cfg = _make_cfg(tmp_path, censys_section={"organization_id": uid})
    assert cfg.get_censys_org_id() == uid


def test_org_id_malformed_raises(tmp_path) -> None:
    bad = "not-a-uuid"
    cfg = _make_cfg(tmp_path, censys_section={"organization_id": bad})
    with pytest.raises(ValueError) as exc_info:
        cfg.get_censys_org_id()
    assert bad not in str(exc_info.value)


# --- credit_profile ---


def test_credit_profile_free_starter_accepted(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"credit_profile": "free_starter"})
    assert cfg.get_censys_credit_profile() == "free_starter"


def test_credit_profile_search_enterprise_accepted(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"credit_profile": "search_enterprise"})
    assert cfg.get_censys_credit_profile() == "search_enterprise"


def test_credit_profile_invalid_coerces_to_free_starter(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"credit_profile": "bad_profile"})
    assert cfg.get_censys_credit_profile() == "free_starter"


def test_credit_profile_case_and_whitespace_normalized(tmp_path) -> None:
    cfg_upper = _make_cfg(tmp_path / "a", censys_section={"credit_profile": " FREE_STARTER "})
    assert cfg_upper.get_censys_credit_profile() == "free_starter"

    cfg_ent = _make_cfg(tmp_path / "b", censys_section={"credit_profile": "SEARCH_ENTERPRISE"})
    assert cfg_ent.get_censys_credit_profile() == "search_enterprise"


# --- defaults ---


def test_defaults_bounds_clamp(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"defaults": {
        "max_pages": 999,
        "page_size": 0,
        "query_hours": 999,
    }})
    d = cfg.get_censys_defaults()
    assert d["max_pages"] == 100
    assert d["page_size"] == 1
    assert d["query_hours"] == 168


def test_defaults_ipv6_string_false_coerces_correctly(tmp_path) -> None:
    cfg_str_false = _make_cfg(tmp_path / "a", censys_section={"defaults": {"ipv6_enabled": "false"}})
    assert cfg_str_false.get_censys_defaults()["ipv6_enabled"] is False

    cfg_str_true = _make_cfg(tmp_path / "b", censys_section={"defaults": {"ipv6_enabled": "true"}})
    assert cfg_str_true.get_censys_defaults()["ipv6_enabled"] is True

    cfg_absent = _make_cfg(tmp_path / "c")
    assert cfg_absent.get_censys_defaults()["ipv6_enabled"] is False


# --- section guard ---


def test_censys_section_malformed_does_not_crash(tmp_path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({"shodan": {"api_key": "test-key"}, "censys": "not-a-dict"}),
        encoding="utf-8",
    )
    cfg = SMBSeekConfig(config_file=str(cfg_file))
    with pytest.raises(ValueError):
        cfg.get_censys_pat()
    assert cfg.get_censys_org_id() is None
    assert cfg.get_censys_credit_profile() == "free_starter"
    d = cfg.get_censys_defaults()
    assert d["max_pages"] == 5
    assert d["ipv6_enabled"] is False


# --- non-regression ---


def test_existing_shodan_accessor_unaffected(tmp_path) -> None:
    cfg = _make_cfg(tmp_path, censys_section={"personal_access_token": "some-pat"})
    assert cfg.get_shodan_api_key() == "test-key"
