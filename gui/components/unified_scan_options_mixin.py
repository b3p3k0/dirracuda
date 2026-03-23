"""
Options/state mixin for UnifiedScanDialog.

Extracted from unified_scan_dialog.py (Slice 13D refactor).
All methods reference ``self.*`` attributes initialized in UnifiedScanDialog.__init__
and resolve correctly via Python's MRO when this mixin is listed as a base class.

Do NOT import from unified_scan_dialog.py — that would create a circular import.
``_CONCURRENCY_UPPER`` and ``_TIMEOUT_UPPER`` are defined here and re-exported
via unified_scan_dialog.py for any existing callers.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

_CONCURRENCY_UPPER = 256
_TIMEOUT_UPPER = 300


class _UnifiedScanDialogOptionsMixin:
    """Mixin — options/state methods only; no ``__init__``."""

    def _load_config_defaults(self) -> None:
        """Load initial concurrency/timeout defaults from config file."""
        config_data: Dict[str, Any] = {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                config_data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
            config_data = {}

        if not isinstance(config_data, dict):
            config_data = {}

        discovery = config_data.get("discovery", {})
        connection = config_data.get("connection", {})

        try:
            disc = int(discovery.get("max_concurrent_hosts", 10))
        except Exception:
            disc = 10
        try:
            timeout = int(connection.get("timeout", 10))
        except Exception:
            timeout = 10

        self.shared_concurrency_var.set(str(max(1, disc)))
        self.shared_timeout_var.set(str(max(1, timeout)))

    def _load_initial_values(self) -> None:
        """Load last-used values from settings manager."""
        if self._settings_manager is None:
            return

        def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
            try:
                v = int(value)
                return v if v >= minimum else default
            except (TypeError, ValueError):
                return default

        def _coerce_bool(value: Any, default: bool = False) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"1", "true", "yes", "on"}:
                    return True
                if normalized in {"0", "false", "no", "off", ""}:
                    return False
            return default

        try:
            self.protocol_smb_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_smb", True), True)
            )
            self.protocol_ftp_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_ftp", True), True)
            )
            self.protocol_http_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.protocol_http", True), True)
            )

            self.max_results_var.set(
                _coerce_int(self._settings_manager.get_setting("unified_scan_dialog.max_shodan_results", 1000), 1000)
            )
            self.custom_filters_var.set(str(self._settings_manager.get_setting("unified_scan_dialog.custom_filters", "")))
            self.country_var.set(str(self._settings_manager.get_setting("unified_scan_dialog.country_code", "")))

            self.shared_concurrency_var.set(
                str(_coerce_int(self._settings_manager.get_setting("unified_scan_dialog.shared_concurrency", 10), 10))
            )
            self.shared_timeout_var.set(
                str(_coerce_int(self._settings_manager.get_setting("unified_scan_dialog.shared_timeout_seconds", 10), 10))
            )

            self.verbose_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.verbose", False), False)
            )
            self.bulk_probe_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_probe_enabled", False), False)
            )
            self.bulk_extract_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_extract_enabled", False), False)
            )
            self.skip_indicator_extract_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.bulk_extract_skip_indicators", True), True)
            )
            self.rce_enabled_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.rce_enabled", False), False)
            )

            mode = str(self._settings_manager.get_setting("unified_scan_dialog.security_mode", "cautious")).strip().lower()
            self.security_mode_var.set(mode if mode in {"cautious", "legacy"} else "cautious")

            self.allow_insecure_tls_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.allow_insecure_tls", True), True)
            )

            self.africa_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_africa", False), False))
            self.asia_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_asia", False), False))
            self.europe_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_europe", False), False))
            self.north_america_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_north_america", False), False)
            )
            self.oceania_var.set(_coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_oceania", False), False))
            self.south_america_var.set(
                _coerce_bool(self._settings_manager.get_setting("unified_scan_dialog.region_south_america", False), False)
            )
        except Exception:
            pass

        # Safety: ensure at least one protocol remains selected.
        if not (self.protocol_smb_var.get() or self.protocol_ftp_var.get() or self.protocol_http_var.get()):
            self.protocol_smb_var.set(True)
            self.protocol_ftp_var.set(True)
            self.protocol_http_var.set(True)

    def _persist_dialog_state(self) -> None:
        """Best-effort persistence of dialog state."""
        if self._settings_manager is None:
            return

        def _coerce_int(value: Any, minimum: int, maximum: int) -> Optional[int]:
            try:
                v = int(str(value).strip())
            except (TypeError, ValueError):
                return None
            if v < minimum or v > maximum:
                return None
            return v

        try:
            self._settings_manager.set_setting("unified_scan_dialog.protocol_smb", bool(self.protocol_smb_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.protocol_ftp", bool(self.protocol_ftp_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.protocol_http", bool(self.protocol_http_var.get()))

            max_results = _coerce_int(self.max_results_var.get(), 1, 1000)
            if max_results is not None:
                self._settings_manager.set_setting("unified_scan_dialog.max_shodan_results", max_results)

            shared_concurrency = _coerce_int(self.shared_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            if shared_concurrency is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_concurrency", shared_concurrency)

            shared_timeout = _coerce_int(self.shared_timeout_var.get(), 1, _TIMEOUT_UPPER)
            if shared_timeout is not None:
                self._settings_manager.set_setting("unified_scan_dialog.shared_timeout_seconds", shared_timeout)

            self._settings_manager.set_setting("unified_scan_dialog.custom_filters", self.custom_filters_var.get().strip())
            self._settings_manager.set_setting("unified_scan_dialog.country_code", self.country_var.get().strip().upper())

            self._settings_manager.set_setting("unified_scan_dialog.verbose", bool(self.verbose_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_enabled", bool(self.bulk_extract_enabled_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.bulk_extract_skip_indicators", bool(self.skip_indicator_extract_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.rce_enabled", bool(self.rce_enabled_var.get()))

            mode = (self.security_mode_var.get() or "cautious").strip().lower()
            if mode not in {"cautious", "legacy"}:
                mode = "cautious"
            self._settings_manager.set_setting("unified_scan_dialog.security_mode", mode)
            self._settings_manager.set_setting("unified_scan_dialog.allow_insecure_tls", bool(self.allow_insecure_tls_var.get()))

            self._settings_manager.set_setting("unified_scan_dialog.region_africa", bool(self.africa_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_asia", bool(self.asia_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_europe", bool(self.europe_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_north_america", bool(self.north_america_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_oceania", bool(self.oceania_var.get()))
            self._settings_manager.set_setting("unified_scan_dialog.region_south_america", bool(self.south_america_var.get()))
        except Exception:
            pass
