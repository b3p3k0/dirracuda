"""
Shodan candidate-cap helpers and dialog used by scan launch flows.

The module name is retained for compatibility with existing imports.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from shared.config import load_config

_CAP_MIN = 1
_CAP_MAX = 100000
_BUDGET_MIN = 1
_BUDGET_MAX = 1000
_TARGET_MIN = 1
_TARGET_MAX = 100000

_SETTING_BASE = "query_cap"
_SETTING_SMB = f"{_SETTING_BASE}.smb_max_shodan_results_per_scan"
_SETTING_FTP = f"{_SETTING_BASE}.ftp_max_shodan_results_per_scan"
_SETTING_HTTP = f"{_SETTING_BASE}.http_max_shodan_results_per_scan"

_LEGACY_SETTING_BASE = "query_budget"
_LEGACY_SETTING_SMB = f"{_LEGACY_SETTING_BASE}.smb_max_query_credits_per_scan"
_LEGACY_SETTING_FTP = f"{_LEGACY_SETTING_BASE}.ftp_max_query_credits_per_scan"
_LEGACY_SETTING_HTTP = f"{_LEGACY_SETTING_BASE}.http_max_query_credits_per_scan"


def _coerce_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _credits_for_cap(cap: Any) -> int:
    cap_int = _coerce_int(cap, 100, minimum=_CAP_MIN, maximum=_CAP_MAX)
    return max(1, min(_BUDGET_MAX, (cap_int + 99) // 100))


def _cap_for_budget(budget: Any, default: int = 1) -> int:
    budget_int = _coerce_int(budget, default, minimum=_BUDGET_MIN, maximum=_BUDGET_MAX)
    return _coerce_int(budget_int * 100, 100, minimum=_CAP_MIN, maximum=_CAP_MAX)


def resolve_config_path_from_settings(settings_manager: Any) -> Optional[str]:
    """Resolve runtime config path from settings manager when available."""
    if settings_manager is None:
        return None

    config_path = None
    try:
        config_path = settings_manager.get_setting("backend.config_path", None)
    except Exception:
        config_path = None

    if not config_path and hasattr(settings_manager, "get_smbseek_config_path"):
        try:
            config_path = settings_manager.get_smbseek_config_path()
        except Exception:
            config_path = None

    return config_path


def load_query_budget_state(settings_manager: Any = None, config_path: Optional[str] = None) -> Dict[str, int]:
    """
    Resolve effective Shodan candidate caps with settings override support.

    Precedence:
    1) GUI candidate-cap settings
    2) legacy GUI budget settings, converted to caps
    3) runtime config budget values, converted to caps
    4) hard defaults
    """
    shodan_cfg = load_config(config_path).get_shodan_config()
    q_limits = shodan_cfg.get("query_limits", {}) if isinstance(shodan_cfg, dict) else {}

    smb_cfg_default = _coerce_int(
        q_limits.get("smb_max_query_credits_per_scan", q_limits.get("max_query_credits_per_scan", 1)),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    ftp_cfg_default = _coerce_int(
        q_limits.get("ftp_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    http_cfg_default = _coerce_int(
        q_limits.get("http_max_query_credits_per_scan", 1),
        1,
        minimum=_BUDGET_MIN,
        maximum=_BUDGET_MAX,
    )
    usable_target = _coerce_int(
        q_limits.get("min_usable_hosts_target", 50),
        50,
        minimum=_TARGET_MIN,
        maximum=_TARGET_MAX,
    )

    smb_cap = _cap_for_budget(smb_cfg_default)
    ftp_cap = _cap_for_budget(ftp_cfg_default)
    http_cap = _cap_for_budget(http_cfg_default)

    if settings_manager is not None:
        try:
            legacy_smb_cap = _cap_for_budget(
                settings_manager.get_setting(_LEGACY_SETTING_SMB, smb_cfg_default)
            )
            legacy_ftp_cap = _cap_for_budget(
                settings_manager.get_setting(_LEGACY_SETTING_FTP, ftp_cfg_default)
            )
            legacy_http_cap = _cap_for_budget(
                settings_manager.get_setting(_LEGACY_SETTING_HTTP, http_cfg_default)
            )
            smb_cap = _coerce_int(
                settings_manager.get_setting(_SETTING_SMB, legacy_smb_cap),
                smb_cap,
                minimum=_CAP_MIN,
                maximum=_CAP_MAX,
            )
            ftp_cap = _coerce_int(
                settings_manager.get_setting(_SETTING_FTP, legacy_ftp_cap),
                ftp_cap,
                minimum=_CAP_MIN,
                maximum=_CAP_MAX,
            )
            http_cap = _coerce_int(
                settings_manager.get_setting(_SETTING_HTTP, legacy_http_cap),
                http_cap,
                minimum=_CAP_MIN,
                maximum=_CAP_MAX,
            )
        except Exception:
            pass

    smb_budget = _credits_for_cap(smb_cap)
    ftp_budget = _credits_for_cap(ftp_cap)
    http_budget = _credits_for_cap(http_cap)

    return {
        "smb_max_shodan_results_per_scan": smb_cap,
        "ftp_max_shodan_results_per_scan": ftp_cap,
        "http_max_shodan_results_per_scan": http_cap,
        "smb_max_query_credits_per_scan": smb_budget,
        "ftp_max_query_credits_per_scan": ftp_budget,
        "http_max_query_credits_per_scan": http_budget,
        "min_usable_hosts_target": usable_target,
    }


def persist_query_budget_state(settings_manager: Any, caps: Dict[str, Any]) -> None:
    """Persist candidate caps to GUI settings storage with legacy budget mirrors."""
    if settings_manager is None:
        return

    smb_cap = _coerce_int(
        caps.get(
            "smb_max_shodan_results_per_scan",
            _cap_for_budget(caps.get("smb_max_query_credits_per_scan", 1)),
        ),
        100,
        minimum=_CAP_MIN,
        maximum=_CAP_MAX,
    )
    ftp_cap = _coerce_int(
        caps.get(
            "ftp_max_shodan_results_per_scan",
            _cap_for_budget(caps.get("ftp_max_query_credits_per_scan", 1)),
        ),
        100,
        minimum=_CAP_MIN,
        maximum=_CAP_MAX,
    )
    http_cap = _coerce_int(
        caps.get(
            "http_max_shodan_results_per_scan",
            _cap_for_budget(caps.get("http_max_query_credits_per_scan", 1)),
        ),
        100,
        minimum=_CAP_MIN,
        maximum=_CAP_MAX,
    )

    smb_budget = _credits_for_cap(smb_cap)
    ftp_budget = _credits_for_cap(ftp_cap)
    http_budget = _credits_for_cap(http_cap)

    try:
        settings_manager.set_setting(_SETTING_SMB, smb_cap)
        settings_manager.set_setting(_SETTING_FTP, ftp_cap)
        settings_manager.set_setting(_SETTING_HTTP, http_cap)
        settings_manager.set_setting(_LEGACY_SETTING_SMB, smb_budget)
        settings_manager.set_setting(_LEGACY_SETTING_FTP, ftp_budget)
        settings_manager.set_setting(_LEGACY_SETTING_HTTP, http_budget)
    except Exception:
        # Best effort only: scan launch flow should never fail on settings write.
        pass
