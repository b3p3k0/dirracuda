"""
Options/state mixin for ScanDialog.

Extracted from scan_dialog.py (Slice 7C refactor).
All methods reference ``self.*`` attributes initialized in ScanDialog.__init__
and resolve correctly via Python's MRO when this mixin is listed as a base class.

Do NOT import from scan_dialog.py — that would create a circular import.
``parse_positive_int`` comes from the shared validators helper module.
"""

from __future__ import annotations

import json
from typing import Optional, Dict, Any

from gui.components.scan_dialog_validators import parse_positive_int


class _ScanDialogOptionsMixin:
    """Mixin — options/state methods only; no ``__init__``."""

    def _load_backend_defaults(self) -> None:
        """Load concurrency and rate limit defaults from the backend configuration."""
        def _coerce_int(value: Any, default: int, minimum: int = 0) -> int:
            try:
                int_value = int(value)
                if int_value < minimum:
                    raise ValueError
                return int_value
            except (TypeError, ValueError):
                return default

        config_data: Dict[str, Any] = {}

        if self._backend_interface is not None:
            try:
                config_data = self._backend_interface.load_effective_config()
            except Exception:
                config_data = {}

        if not config_data:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as config_file:
                    config_data = json.load(config_file)
            except (FileNotFoundError, json.JSONDecodeError, PermissionError):
                config_data = {}

        if not isinstance(config_data, dict):
            config_data = {}

        discovery_defaults = config_data.get('discovery', {}) if isinstance(config_data.get('discovery'), dict) else {}
        access_defaults = config_data.get('access', {}) if isinstance(config_data.get('access'), dict) else {}
        connection_defaults = config_data.get('connection', {}) if isinstance(config_data.get('connection'), dict) else {}

        discovery_value = _coerce_int(discovery_defaults.get('max_concurrent_hosts'), 1, minimum=1)
        access_value = _coerce_int(access_defaults.get('max_concurrent_hosts'), 1, minimum=1)
        rate_limit_value = _coerce_int(connection_defaults.get('rate_limit_delay'), 1, minimum=0)
        share_delay_value = _coerce_int(connection_defaults.get('share_access_delay'), 1, minimum=0)

        self.discovery_concurrency_var.set(str(discovery_value))
        self.access_concurrency_var.set(str(access_value))
        self.rate_limit_delay_var.set(str(rate_limit_value))
        self.share_access_delay_var.set(str(share_delay_value))

    def _build_scan_options(self, country_param: Optional[str]) -> Dict[str, Any]:
        """
        Build complete scan options dict with type-safe settings extraction.

        Args:
            country_param: Country code(s) from user input

        Returns:
            Complete scan options dict with all keys ScanManager expects
        """
        # Get values from UI (user's current selections)
        max_results = self.max_results_var.get()

        # Handle recent hours (empty string means None)
        recent_hours_text = self.recent_hours_var.get().strip()
        recent_hours = int(recent_hours_text) if recent_hours_text else None

        rescan_all = self.rescan_all_var.get()
        rescan_failed = self.rescan_failed_var.get()
        security_mode = (self.security_mode_var.get() or "cautious").strip().lower()
        if security_mode not in {"cautious", "legacy"}:
            security_mode = "cautious"
        verbose_enabled = bool(self.verbose_var.get())

        # Handle API key (empty string means None)
        api_key = self.api_key_var.get().strip()
        api_key = api_key if api_key else None

        # Handle custom filters
        custom_filters = self.custom_filters_var.get().strip()

        discovery_concurrency = parse_positive_int(
            self.discovery_concurrency_var.get().strip(),
            "Discovery max concurrent hosts",
            minimum=1,
            maximum=self._concurrency_upper_limit
        )

        access_concurrency = parse_positive_int(
            self.access_concurrency_var.get().strip(),
            "Access max concurrent hosts",
            minimum=1,
            maximum=self._concurrency_upper_limit
        )

        rate_limit_delay = parse_positive_int(
            self.rate_limit_delay_var.get().strip(),
            "Rate limit delay (seconds)",
            minimum=0,
            maximum=self._delay_upper_limit
        )

        share_access_delay = parse_positive_int(
            self.share_access_delay_var.get().strip(),
            "Share access delay (seconds)",
            minimum=0,
            maximum=self._delay_upper_limit
        )

        # Save selections back to settings for next time
        if self._settings_manager is not None:
            try:
                self._settings_manager.set_setting('scan_dialog.max_shodan_results', max_results)
                self._settings_manager.set_setting('scan_dialog.recent_hours', recent_hours)
                self._settings_manager.set_setting('scan_dialog.rescan_all', rescan_all)
                self._settings_manager.set_setting('scan_dialog.rescan_failed', rescan_failed)
                self._settings_manager.set_setting('scan_dialog.api_key_override', api_key or '')
                self._settings_manager.set_setting('scan_dialog.custom_filters', custom_filters)
                # Save only manually entered country codes, not region-selected ones
                manual_country_input = self.country_var.get().strip()
                self._settings_manager.set_setting('scan_dialog.country_code', manual_country_input)
                self._settings_manager.set_setting('scan_dialog.discovery_max_concurrency', discovery_concurrency)
                self._settings_manager.set_setting('scan_dialog.access_max_concurrency', access_concurrency)
                self._settings_manager.set_setting('scan_dialog.rate_limit_delay', rate_limit_delay)
                self._settings_manager.set_setting('scan_dialog.share_access_delay', share_access_delay)
                self._settings_manager.set_setting('scan_dialog.security_mode', security_mode)
                self._settings_manager.set_setting('scan_dialog.verbose', verbose_enabled)
                self._settings_manager.set_setting('scan_dialog.rce_enabled', self.rce_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_probe_enabled', self.bulk_probe_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_extract_enabled', self.bulk_extract_enabled_var.get())
                self._settings_manager.set_setting('scan_dialog.bulk_extract_skip_indicators', self.skip_indicator_extract_var.get())

                # Save region selections
                self._settings_manager.set_setting('scan_dialog.region_africa', self.africa_var.get())
                self._settings_manager.set_setting('scan_dialog.region_asia', self.asia_var.get())
                self._settings_manager.set_setting('scan_dialog.region_europe', self.europe_var.get())
                self._settings_manager.set_setting('scan_dialog.region_north_america', self.north_america_var.get())
                self._settings_manager.set_setting('scan_dialog.region_oceania', self.oceania_var.get())
                self._settings_manager.set_setting('scan_dialog.region_south_america', self.south_america_var.get())
            except Exception:
                pass  # Don't fail scan if settings save fails

        # Build complete scan options dict
        scan_options = {
            'country': country_param,
            'max_shodan_results': max_results,
            'recent_hours': recent_hours,
            'rescan_all': rescan_all,
            'rescan_failed': rescan_failed,
            'api_key_override': api_key,
            'custom_filters': custom_filters,
            'discovery_max_concurrent_hosts': discovery_concurrency,
            'access_max_concurrent_hosts': access_concurrency,
            'rate_limit_delay': rate_limit_delay,
            'share_access_delay': share_access_delay,
            'security_mode': security_mode,
            'verbose': verbose_enabled,
            'rce_enabled': self.rce_enabled_var.get(),
            'bulk_probe_enabled': self.bulk_probe_enabled_var.get(),
            'bulk_extract_enabled': self.bulk_extract_enabled_var.get(),
            'bulk_extract_skip_indicators': self.skip_indicator_extract_var.get()
        }

        return scan_options

    def _load_initial_values(self) -> None:
        """Load initial values from settings manager into UI variables."""
        if self._settings_manager is not None:
            try:
                # Load saved settings into UI variables
                max_results = int(self._settings_manager.get_setting('scan_dialog.max_shodan_results', 1000))
                recent_hours = self._settings_manager.get_setting('scan_dialog.recent_hours', None)
                rescan_all = bool(self._settings_manager.get_setting('scan_dialog.rescan_all', False))
                rescan_failed = bool(self._settings_manager.get_setting('scan_dialog.rescan_failed', False))
                api_key = str(self._settings_manager.get_setting('scan_dialog.api_key_override', ''))
                custom_filters = str(self._settings_manager.get_setting('scan_dialog.custom_filters', ''))
                country_code = str(self._settings_manager.get_setting('scan_dialog.country_code', ''))

                discovery_concurrency = self._settings_manager.get_setting('scan_dialog.discovery_max_concurrency', None)
                access_concurrency = self._settings_manager.get_setting('scan_dialog.access_max_concurrency', None)
                rate_limit_delay = self._settings_manager.get_setting('scan_dialog.rate_limit_delay', None)
                share_access_delay = self._settings_manager.get_setting('scan_dialog.share_access_delay', None)
                security_mode = self._settings_manager.get_setting('scan_dialog.security_mode', 'cautious')
                verbose_enabled = bool(self._settings_manager.get_setting('scan_dialog.verbose', False))

                # Set UI variables
                self.max_results_var.set(max_results)
                self.recent_hours_var.set(str(recent_hours) if recent_hours is not None else '')
                self.rescan_all_var.set(rescan_all)
                self.rescan_failed_var.set(rescan_failed)
                self.api_key_var.set(api_key)
                self.custom_filters_var.set(custom_filters)
                self.country_var.set(country_code)

                if discovery_concurrency is not None:
                    self.discovery_concurrency_var.set(str(discovery_concurrency))
                if access_concurrency is not None:
                    self.access_concurrency_var.set(str(access_concurrency))
                if rate_limit_delay is not None:
                    self.rate_limit_delay_var.set(str(rate_limit_delay))
                if share_access_delay is not None:
                    self.share_access_delay_var.set(str(share_access_delay))
                if security_mode in ("cautious", "legacy"):
                    self.security_mode_var.set(security_mode)
                self.verbose_var.set(verbose_enabled)

                # Load RCE analysis setting
                rce_enabled = bool(self._settings_manager.get_setting('scan_dialog.rce_enabled', False))
                self.rce_enabled_var.set(rce_enabled)

                # Load bulk operation settings
                bulk_probe_enabled = bool(self._settings_manager.get_setting('scan_dialog.bulk_probe_enabled', False))
                bulk_extract_enabled = bool(self._settings_manager.get_setting('scan_dialog.bulk_extract_enabled', False))
                skip_extract_indicators = bool(self._settings_manager.get_setting('scan_dialog.bulk_extract_skip_indicators', True))
                self.bulk_probe_enabled_var.set(bulk_probe_enabled)
                self.bulk_extract_enabled_var.set(bulk_extract_enabled)
                self.skip_indicator_extract_var.set(skip_extract_indicators)

                # Load region selections
                africa = bool(self._settings_manager.get_setting('scan_dialog.region_africa', False))
                asia = bool(self._settings_manager.get_setting('scan_dialog.region_asia', False))
                europe = bool(self._settings_manager.get_setting('scan_dialog.region_europe', False))
                north_america = bool(self._settings_manager.get_setting('scan_dialog.region_north_america', False))
                oceania = bool(self._settings_manager.get_setting('scan_dialog.region_oceania', False))
                south_america = bool(self._settings_manager.get_setting('scan_dialog.region_south_america', False))

                # Set region variables
                self.africa_var.set(africa)
                self.asia_var.set(asia)
                self.europe_var.set(europe)
                self.north_america_var.set(north_america)
                self.oceania_var.set(oceania)
                self.south_america_var.set(south_america)
            except Exception:
                # Fall back to defaults if settings loading fails
                pass

        # (Query preview removed)

    def _persist_quick_settings(self) -> None:
        """
        Best-effort persistence for worker counts and security mode so they survive
        app restarts even when the user cancels instead of starting a scan.
        """
        if self._settings_manager is None:
            return

        def _coerce_int(value: str, minimum: int, maximum: int) -> Optional[int]:
            try:
                num = int(str(value).strip())
                if num < minimum or num > maximum:
                    return None
                return num
            except Exception:
                return None

        try:
            discovery_val = _coerce_int(self.discovery_concurrency_var.get(), 1, self._concurrency_upper_limit)
            access_val = _coerce_int(self.access_concurrency_var.get(), 1, self._concurrency_upper_limit)
            if discovery_val is not None:
                self._settings_manager.set_setting('scan_dialog.discovery_max_concurrency', discovery_val)
            if access_val is not None:
                self._settings_manager.set_setting('scan_dialog.access_max_concurrency', access_val)

            security_mode = (self.security_mode_var.get() or "cautious").strip().lower()
            if security_mode not in {"cautious", "legacy"}:
                security_mode = "cautious"
            self._settings_manager.set_setting('scan_dialog.security_mode', security_mode)
            self._settings_manager.set_setting('scan_dialog.verbose', bool(self.verbose_var.get()))
        except Exception:
            # Persistence is best-effort; ignore failures here
            pass
