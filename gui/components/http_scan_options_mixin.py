"""
HTTP Scan Options Mixin

Handles config-file defaults loading and settings-manager persistence for
HttpScanDialog.  Extracted as a mixin (Slice 12A) to keep http_scan_dialog.py
below 1000 lines.

No imports from gui.components.http_scan_dialog — avoids circular imports.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

_CONCURRENCY_UPPER = 256
_TIMEOUT_UPPER = 300


class _HttpScanOptionsMixin:

    # ------------------------------------------------------------------
    # Config defaults
    # ------------------------------------------------------------------

    def _load_config_defaults(self) -> None:
        """Load HTTP concurrency / timeout defaults from the config file."""
        config_data: Dict[str, Any] = {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                config_data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
            config_data = {}

        if not isinstance(config_data, dict):
            config_data = {}

        def _safe_dict(d: Any, key: str) -> Dict:
            sub = d.get(key, {})
            return sub if isinstance(sub, dict) else {}

        http_cfg = _safe_dict(config_data, "http")
        disc_cfg = _safe_dict(http_cfg, "discovery")
        verif_cfg = _safe_dict(http_cfg, "verification")

        def _coerce(value: Any, default: int, minimum: int = 1) -> int:
            try:
                v = int(value)
                return v if v >= minimum else default
            except (TypeError, ValueError):
                return default

        self.discovery_concurrency_var.set(
            str(_coerce(disc_cfg.get("max_concurrent_hosts"), 10))
        )
        self.connect_timeout_var.set(
            str(_coerce(verif_cfg.get("connect_timeout"), 5))
        )
        self.request_timeout_var.set(
            str(_coerce(verif_cfg.get("request_timeout"), 15))
        )

    def _load_initial_values(self) -> None:
        """Load last-used dialog values from settings manager when available."""
        if self._settings_manager is None:
            return

        def _coerce_int(value: Any, default: int, minimum: int = 1) -> int:
            try:
                v = int(value)
                return v if v >= minimum else default
            except (TypeError, ValueError):
                return default

        try:
            max_results = _coerce_int(
                self._settings_manager.get_setting("http_scan_dialog.max_shodan_results", 1000),
                1000,
            )
            api_key = str(self._settings_manager.get_setting("http_scan_dialog.api_key_override", ""))
            country_code = str(self._settings_manager.get_setting("http_scan_dialog.country_code", ""))
            custom_filters = str(self._settings_manager.get_setting("http_scan_dialog.custom_filters", ""))

            discovery_workers = _coerce_int(
                self._settings_manager.get_setting("http_scan_dialog.discovery_max_concurrent_hosts", 10),
                10,
            )
            connect_timeout = _coerce_int(
                self._settings_manager.get_setting("http_scan_dialog.connect_timeout", 5),
                5,
            )
            request_timeout = _coerce_int(
                self._settings_manager.get_setting("http_scan_dialog.request_timeout", 15),
                15,
            )
            allow_insecure_tls = bool(
                self._settings_manager.get_setting("http_scan_dialog.allow_insecure_tls", True)
            )
            verbose = bool(self._settings_manager.get_setting("http_scan_dialog.verbose", False))
            bulk_probe_enabled = bool(
                self._settings_manager.get_setting("http_scan_dialog.bulk_probe_enabled", False)
            )

            africa = bool(self._settings_manager.get_setting("http_scan_dialog.region_africa", False))
            asia = bool(self._settings_manager.get_setting("http_scan_dialog.region_asia", False))
            europe = bool(self._settings_manager.get_setting("http_scan_dialog.region_europe", False))
            north_america = bool(
                self._settings_manager.get_setting("http_scan_dialog.region_north_america", False)
            )
            oceania = bool(self._settings_manager.get_setting("http_scan_dialog.region_oceania", False))
            south_america = bool(
                self._settings_manager.get_setting("http_scan_dialog.region_south_america", False)
            )

            self.max_results_var.set(max_results)
            self.api_key_var.set(api_key)
            self.country_var.set(country_code)
            self.custom_filters_var.set(custom_filters)
            self.discovery_concurrency_var.set(str(discovery_workers))
            self.connect_timeout_var.set(str(connect_timeout))
            self.request_timeout_var.set(str(request_timeout))
            self.allow_insecure_tls_var.set(allow_insecure_tls)
            self.verbose_var.set(verbose)
            self.bulk_probe_enabled_var.set(bulk_probe_enabled)

            self.africa_var.set(africa)
            self.asia_var.set(asia)
            self.europe_var.set(europe)
            self.north_america_var.set(north_america)
            self.oceania_var.set(oceania)
            self.south_america_var.set(south_america)
        except Exception:
            # Best-effort only; defaults remain in place if settings are unavailable.
            pass

    def _persist_dialog_state(self) -> None:
        """
        Best-effort persistence so dialog changes survive reopen even if user cancels.
        """
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
            max_results = _coerce_int(self.max_results_var.get(), 1, 1000)
            if max_results is not None:
                self._settings_manager.set_setting("http_scan_dialog.max_shodan_results", max_results)

            disc = _coerce_int(self.discovery_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            conn_to = _coerce_int(self.connect_timeout_var.get(), 1, _TIMEOUT_UPPER)
            req_to = _coerce_int(self.request_timeout_var.get(), 1, _TIMEOUT_UPPER)

            if disc is not None:
                self._settings_manager.set_setting("http_scan_dialog.discovery_max_concurrent_hosts", disc)
            if conn_to is not None:
                self._settings_manager.set_setting("http_scan_dialog.connect_timeout", conn_to)
            if req_to is not None:
                self._settings_manager.set_setting("http_scan_dialog.request_timeout", req_to)

            self._settings_manager.set_setting("http_scan_dialog.api_key_override", self.api_key_var.get().strip())
            self._settings_manager.set_setting("http_scan_dialog.country_code", self.country_var.get().strip().upper())
            self._settings_manager.set_setting("http_scan_dialog.custom_filters", self.custom_filters_var.get().strip())
            self._settings_manager.set_setting(
                "http_scan_dialog.allow_insecure_tls", bool(self.allow_insecure_tls_var.get())
            )
            self._settings_manager.set_setting("http_scan_dialog.verbose", bool(self.verbose_var.get()))
            self._settings_manager.set_setting(
                "http_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get())
            )

            self._settings_manager.set_setting("http_scan_dialog.region_africa", bool(self.africa_var.get()))
            self._settings_manager.set_setting("http_scan_dialog.region_asia", bool(self.asia_var.get()))
            self._settings_manager.set_setting("http_scan_dialog.region_europe", bool(self.europe_var.get()))
            self._settings_manager.set_setting(
                "http_scan_dialog.region_north_america", bool(self.north_america_var.get())
            )
            self._settings_manager.set_setting("http_scan_dialog.region_oceania", bool(self.oceania_var.get()))
            self._settings_manager.set_setting(
                "http_scan_dialog.region_south_america", bool(self.south_america_var.get())
            )
        except Exception:
            # Persistence is best-effort; do not block close/start on errors.
            pass
