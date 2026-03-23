"""
FTP Scan Options Mixin

Handles config-file defaults loading and settings-manager persistence for
FtpScanDialog.  Extracted as a mixin (Slice 9B) to keep ftp_scan_dialog.py
below 1000 lines.

No imports from gui.components.ftp_scan_dialog — avoids circular imports.
"""

from __future__ import annotations

import json
import tkinter as tk
from typing import Any, Dict, Optional

_CONCURRENCY_UPPER = 256
_TIMEOUT_UPPER = 300


class _FtpScanOptionsMixin:

    # ------------------------------------------------------------------
    # Config defaults
    # ------------------------------------------------------------------

    def _load_config_defaults(self) -> None:
        """Load FTP concurrency / timeout defaults from the config file."""
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

        ftp_cfg = _safe_dict(config_data, "ftp")
        disc_cfg = _safe_dict(ftp_cfg, "discovery")
        acc_cfg = _safe_dict(ftp_cfg, "access")
        verif_cfg = _safe_dict(ftp_cfg, "verification")

        def _coerce(value: Any, default: int, minimum: int = 1) -> int:
            try:
                v = int(value)
                return v if v >= minimum else default
            except (TypeError, ValueError):
                return default

        self.discovery_concurrency_var.set(
            str(_coerce(disc_cfg.get("max_concurrent_hosts"), 10))
        )
        self.access_concurrency_var.set(
            str(_coerce(acc_cfg.get("max_concurrent_hosts"), 4))
        )
        self.connect_timeout_var.set(
            str(_coerce(verif_cfg.get("connect_timeout"), 5))
        )
        self.auth_timeout_var.set(
            str(_coerce(verif_cfg.get("auth_timeout"), 10))
        )
        self.listing_timeout_var.set(
            str(_coerce(verif_cfg.get("listing_timeout"), 15))
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
            max_results = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.max_shodan_results", 1000),
                1000,
            )
            api_key = str(self._settings_manager.get_setting("ftp_scan_dialog.api_key_override", ""))
            country_code = str(self._settings_manager.get_setting("ftp_scan_dialog.country_code", ""))
            custom_filters = str(self._settings_manager.get_setting("ftp_scan_dialog.custom_filters", ""))

            discovery_workers = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.discovery_max_concurrent_hosts", 10),
                10,
            )
            access_workers = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.access_max_concurrent_hosts", 4),
                4,
            )
            connect_timeout = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.connect_timeout", 5),
                5,
            )
            auth_timeout = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.auth_timeout", 10),
                10,
            )
            listing_timeout = _coerce_int(
                self._settings_manager.get_setting("ftp_scan_dialog.listing_timeout", 15),
                15,
            )
            verbose = _coerce_bool(
                self._settings_manager.get_setting("ftp_scan_dialog.verbose", False)
            )
            bulk_probe_enabled = _coerce_bool(
                self._settings_manager.get_setting("ftp_scan_dialog.bulk_probe_enabled", False)
            )

            africa = _coerce_bool(self._settings_manager.get_setting("ftp_scan_dialog.region_africa", False))
            asia = _coerce_bool(self._settings_manager.get_setting("ftp_scan_dialog.region_asia", False))
            europe = _coerce_bool(self._settings_manager.get_setting("ftp_scan_dialog.region_europe", False))
            north_america = _coerce_bool(
                self._settings_manager.get_setting("ftp_scan_dialog.region_north_america", False)
            )
            oceania = _coerce_bool(self._settings_manager.get_setting("ftp_scan_dialog.region_oceania", False))
            south_america = _coerce_bool(
                self._settings_manager.get_setting("ftp_scan_dialog.region_south_america", False)
            )

            self.max_results_var.set(max_results)
            self.api_key_var.set(api_key)
            self.country_var.set(country_code)
            self.custom_filters_var.set(custom_filters)
            self.discovery_concurrency_var.set(str(discovery_workers))
            self.access_concurrency_var.set(str(access_workers))
            self.connect_timeout_var.set(str(connect_timeout))
            self.auth_timeout_var.set(str(auth_timeout))
            self.listing_timeout_var.set(str(listing_timeout))
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
                self._settings_manager.set_setting("ftp_scan_dialog.max_shodan_results", max_results)

            disc = _coerce_int(self.discovery_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            acc = _coerce_int(self.access_concurrency_var.get(), 1, _CONCURRENCY_UPPER)
            conn_to = _coerce_int(self.connect_timeout_var.get(), 1, _TIMEOUT_UPPER)
            auth_to = _coerce_int(self.auth_timeout_var.get(), 1, _TIMEOUT_UPPER)
            list_to = _coerce_int(self.listing_timeout_var.get(), 1, _TIMEOUT_UPPER)

            if disc is not None:
                self._settings_manager.set_setting("ftp_scan_dialog.discovery_max_concurrent_hosts", disc)
            if acc is not None:
                self._settings_manager.set_setting("ftp_scan_dialog.access_max_concurrent_hosts", acc)
            if conn_to is not None:
                self._settings_manager.set_setting("ftp_scan_dialog.connect_timeout", conn_to)
            if auth_to is not None:
                self._settings_manager.set_setting("ftp_scan_dialog.auth_timeout", auth_to)
            if list_to is not None:
                self._settings_manager.set_setting("ftp_scan_dialog.listing_timeout", list_to)

            self._settings_manager.set_setting("ftp_scan_dialog.api_key_override", self.api_key_var.get().strip())
            self._settings_manager.set_setting("ftp_scan_dialog.country_code", self.country_var.get().strip().upper())
            self._settings_manager.set_setting("ftp_scan_dialog.custom_filters", self.custom_filters_var.get().strip())
            self._settings_manager.set_setting("ftp_scan_dialog.verbose", bool(self.verbose_var.get()))
            self._settings_manager.set_setting(
                "ftp_scan_dialog.bulk_probe_enabled", bool(self.bulk_probe_enabled_var.get())
            )

            self._settings_manager.set_setting("ftp_scan_dialog.region_africa", bool(self.africa_var.get()))
            self._settings_manager.set_setting("ftp_scan_dialog.region_asia", bool(self.asia_var.get()))
            self._settings_manager.set_setting("ftp_scan_dialog.region_europe", bool(self.europe_var.get()))
            self._settings_manager.set_setting(
                "ftp_scan_dialog.region_north_america", bool(self.north_america_var.get())
            )
            self._settings_manager.set_setting("ftp_scan_dialog.region_oceania", bool(self.oceania_var.get()))
            self._settings_manager.set_setting(
                "ftp_scan_dialog.region_south_america", bool(self.south_america_var.get())
            )
        except Exception:
            # Persistence is best-effort; do not block close/start on errors.
            pass
