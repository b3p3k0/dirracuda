"""C2: webui/config.py tests."""

import json
import pytest

from webui.config import (
    TLSConfig,
    WebUIConfig,
    WebUIConfigError,
    load_config,
    save_config,
    validate,
)


def _remote_config(**kwargs):
    """Return a minimal valid non-loopback WebUIConfig, overrideable via kwargs."""
    tls = kwargs.pop("tls", TLSConfig(enabled=True))
    cfg = WebUIConfig(
        bind_address="192.168.1.1",
        remote_enabled=True,
        allowed_cidrs=["192.168.1.0/24"],
        tls=tls,
    )
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def test_defaults_are_loopback_safe():
    validate(WebUIConfig())


def test_loopback_tls_disabled_ok():
    cfg = WebUIConfig(tls=TLSConfig(enabled=False))
    validate(cfg)


def test_nonloopback_remote_disabled_fails():
    cfg = _remote_config(remote_enabled=False)
    with pytest.raises(WebUIConfigError, match="remote_enabled"):
        validate(cfg)


def test_nonloopback_empty_cidrs_fails():
    cfg = _remote_config(allowed_cidrs=[])
    with pytest.raises(WebUIConfigError, match="allowed_cidrs"):
        validate(cfg)


def test_nonloopback_tls_off_no_override_fails():
    cfg = _remote_config(tls=TLSConfig(enabled=False, allow_insecure_remote=False))
    with pytest.raises(WebUIConfigError, match="allow_insecure_remote"):
        validate(cfg)


def test_nonloopback_tls_off_with_override_ok():
    cfg = _remote_config(tls=TLSConfig(enabled=False, allow_insecure_remote=True))
    validate(cfg)


def test_valid_remote_config_ok():
    validate(_remote_config())


def test_bad_cidr_entry_fails():
    cfg = WebUIConfig(allowed_cidrs=["not-a-cidr"])
    with pytest.raises(WebUIConfigError, match="CIDR"):
        validate(cfg)


def test_port_out_of_range_fails():
    with pytest.raises(WebUIConfigError, match="port"):
        validate(WebUIConfig(port=0))
    with pytest.raises(WebUIConfigError, match="port"):
        validate(WebUIConfig(port=99999))


def test_session_idle_bounds():
    with pytest.raises(WebUIConfigError, match="session_timeout_idle"):
        validate(WebUIConfig(session_timeout_idle=299))
    with pytest.raises(WebUIConfigError, match="session_timeout_idle"):
        validate(WebUIConfig(session_timeout_idle=14401))


def test_session_absolute_bounds():
    with pytest.raises(WebUIConfigError, match="session_timeout_absolute"):
        validate(WebUIConfig(session_timeout_absolute=3599))


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nonexistent.json")
    assert cfg == WebUIConfig()


def test_atomic_write_and_read_back(tmp_path):
    p = tmp_path / "webui.json"
    cfg = WebUIConfig(port=5481)
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.port == 5481
    assert loaded.tls.enabled is True


def test_file_mode_restricted(tmp_path):
    import stat
    p = tmp_path / "webui.json"
    save_config(WebUIConfig(), p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text("{bad json", encoding="utf-8")
    with pytest.raises(WebUIConfigError):
        load_config(p)


def test_unknown_toplevel_key_rejected(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text(json.dumps({"foo": 1}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="Unknown config keys"):
        load_config(p)


def test_unknown_tls_key_rejected(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text(json.dumps({"tls": {"foo": 1}}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="Unknown tls keys"):
        load_config(p)


def test_wrong_type_port(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text(json.dumps({"port": "5480"}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="'port'"):
        load_config(p)


def test_bool_rejected_for_int_field(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text(json.dumps({"port": True}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="bool"):
        load_config(p)


def test_allowed_cidrs_wrong_shape(tmp_path):
    p = tmp_path / "webui.json"
    p.write_text(json.dumps({"allowed_cidrs": "127.0.0.1/32"}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="list"):
        load_config(p)
    p.write_text(json.dumps({"allowed_cidrs": [123]}), encoding="utf-8")
    with pytest.raises(WebUIConfigError, match="str"):
        load_config(p)


def test_bool_port_dataclass_rejected():
    with pytest.raises(WebUIConfigError, match="bool"):
        validate(WebUIConfig(port=True))


def test_str_enabled_dataclass_rejected():
    with pytest.raises(WebUIConfigError, match="'enabled'"):
        validate(WebUIConfig(enabled="false"))


def test_str_tls_enabled_dataclass_rejected():
    with pytest.raises(WebUIConfigError, match="'tls.enabled'"):
        validate(WebUIConfig(tls=TLSConfig(enabled="false")))
