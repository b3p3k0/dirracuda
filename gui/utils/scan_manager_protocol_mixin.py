"""
ScanManager Protocol Worker Mixin

Contains the FTP and HTTP scan start + worker methods extracted from ScanManager.
No imports from scan_manager.py (avoids circular import).
"""

import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from gui.utils.backend_interface import BackendInterface


class _ScanManagerProtocolMixin:
    """Private mixin: FTP/HTTP scan start + worker methods for ScanManager."""

    def start_ftp_scan(
        self,
        scan_options: dict,
        backend_path: str,
        progress_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
        config_path: Optional[str] = None,
    ) -> bool:
        """
        Start an FTP scan in a background thread.

        Shares the same lock/state mechanism as start_scan() so only one
        protocol scan can run at a time. SMB behaviour is unchanged.

        Args:
            scan_options: Dict with optional 'country' key.
            backend_path: Path to SMBSeek installation directory.
            progress_callback: Called with (percentage, status, phase).
            log_callback: Called with raw stdout lines for log streaming.
            config_path: Optional absolute/relative config file to force for CLI runs.

        Returns:
            True if scan started, False if already scanning or lock failed.
        """
        if self.is_scan_active():
            return False

        country = scan_options.get("country")
        if not self.create_lock_file(country, "ftp"):
            return False

        try:
            self.backend_interface = BackendInterface(backend_path)
            if config_path:
                self.backend_interface.config_path = Path(config_path).expanduser().resolve()
            self.is_scanning = True
            self.scan_start_time = datetime.now()
            self.progress_callback = progress_callback
            self.log_callback = log_callback
            self.scan_results = {
                "start_time": self.scan_start_time.isoformat(),
                "country": country,
                "scan_options": scan_options,
                "status": "running",
                "protocol": "ftp",
            }

            self.scan_thread = threading.Thread(
                target=self._ftp_scan_worker,
                args=(scan_options,),
                daemon=True,
            )
            self.scan_thread.start()
            return True

        except Exception as exc:
            self.is_scanning = False
            self.remove_lock_file()
            self._update_progress(0, f"Failed to start FTP scan: {exc}", "error")
            return False

    def _ftp_scan_worker(self, scan_options: dict) -> None:
        """
        Worker thread for FTP scan execution.

        Mirrors _scan_worker() structure exactly:
        - try: build config_overrides from scan_options, execute under
               _temporary_config_override (if any), _process_scan_results()
        - except: _handle_scan_error()
        - finally: _cleanup_scan() — always runs

        Progress updates go through _update_progress() for thread-safe UI
        dispatch via ui_dispatcher.
        """
        try:
            country_raw = scan_options.get("country") or ""
            countries = [c.strip() for c in country_raw.split(",") if c.strip()]

            self._update_progress(5, "Initializing FTP scan...", "initialization")

            # Build runtime config overrides from dialog options.
            config_overrides = {}

            # Shodan API key (shared global path, same as SMB).
            api_key = scan_options.get("api_key_override")
            if api_key:
                config_overrides["shodan"] = {"api_key": api_key}

            # FTP Shodan query limits.
            max_results = scan_options.get("max_shodan_results")
            if max_results is not None:
                (config_overrides
                 .setdefault("ftp", {})
                 .setdefault("shodan", {})
                 .setdefault("query_limits", {})
                 )["max_results"] = max_results

            # FTP discovery concurrency (key matches SMB naming convention).
            disc_conc = scan_options.get("discovery_max_concurrent_hosts")
            if disc_conc is not None:
                config_overrides.setdefault("ftp", {}).setdefault("discovery", {})[
                    "max_concurrent_hosts"
                ] = disc_conc

            # FTP access concurrency.
            acc_conc = scan_options.get("access_max_concurrent_hosts")
            if acc_conc is not None:
                config_overrides.setdefault("ftp", {}).setdefault("access", {})[
                    "max_concurrent_hosts"
                ] = acc_conc

            # FTP timeouts.
            verif_overrides = {}
            for key in ("connect_timeout", "auth_timeout", "listing_timeout"):
                val = scan_options.get(key)
                if val is not None:
                    verif_overrides[key] = val
            if verif_overrides:
                config_overrides.setdefault("ftp", {})["verification"] = verif_overrides

            verbose = bool(scan_options.get("verbose", False))
            custom_filters = scan_options.get("custom_filters", "")

            if config_overrides:
                self._update_progress(7, "Applying configuration overrides...", "initialization")
                with self.backend_interface._temporary_config_override(config_overrides):
                    result = self.backend_interface.run_ftp_scan(
                        countries=countries,
                        progress_callback=self._handle_backend_progress,
                        log_callback=self._handle_backend_log_line,
                        filters=custom_filters,
                        verbose=verbose,
                    )
            else:
                result = self.backend_interface.run_ftp_scan(
                    countries=countries,
                    progress_callback=self._handle_backend_progress,
                    log_callback=self._handle_backend_log_line,
                    filters=custom_filters,
                    verbose=verbose,
                )

            self._process_scan_results(result)

        except Exception as exc:
            self._handle_scan_error(exc)

        finally:
            self._cleanup_scan()

    def start_http_scan(
        self,
        scan_options: dict,
        backend_path: str,
        progress_callback: Callable,
        log_callback: Optional[Callable[[str], None]] = None,
        config_path: Optional[str] = None,
    ) -> bool:
        """
        Start an HTTP scan in a background thread.

        Shares the same lock/state mechanism as start_scan() and start_ftp_scan()
        so only one protocol scan can run at a time. SMB and FTP behaviour unchanged.

        Args:
            scan_options: Dict from HttpScanDialog._build_scan_options().
            backend_path: Path to SMBSeek installation directory.
            progress_callback: Called with (percentage, status, phase).
            log_callback: Called with raw stdout lines for log streaming.
            config_path: Optional absolute/relative config file to force for CLI runs.

        Returns:
            True if scan started, False if already scanning or lock failed.
        """
        if self.is_scan_active():
            return False

        country = scan_options.get("country")
        if not self.create_lock_file(country, "http"):
            return False

        try:
            self.backend_interface = BackendInterface(backend_path)
            if config_path:
                self.backend_interface.config_path = Path(config_path).expanduser().resolve()
            self.is_scanning = True
            self.scan_start_time = datetime.now()
            self.progress_callback = progress_callback
            self.log_callback = log_callback
            self.scan_results = {
                "start_time": self.scan_start_time.isoformat(),
                "country": country,
                "scan_options": scan_options,
                "status": "running",
                "protocol": "http",
            }

            self.scan_thread = threading.Thread(
                target=self._http_scan_worker,
                args=(scan_options,),
                daemon=True,
            )
            self.scan_thread.start()
            return True

        except Exception as exc:
            self.is_scanning = False
            self.remove_lock_file()
            self._update_progress(0, f"Failed to start HTTP scan: {exc}", "error")
            return False

    def _http_scan_worker(self, scan_options: dict) -> None:
        """
        Worker thread for HTTP scan execution.

        Mirrors _ftp_scan_worker() structure exactly:
        - try: build config_overrides from scan_options, execute under
               _temporary_config_override (if any), _process_scan_results()
        - except: _handle_scan_error()
        - finally: _cleanup_scan() — always runs
        """
        try:
            country_raw = scan_options.get("country") or ""
            countries = [c.strip() for c in country_raw.split(",") if c.strip()]

            self._update_progress(5, "Initializing HTTP scan...", "initialization")

            # Build runtime config overrides from dialog options.
            config_overrides = {}

            # Shodan API key (shared global path, same as SMB/FTP).
            api_key = scan_options.get("api_key_override")
            if api_key:
                config_overrides["shodan"] = {"api_key": api_key}

            # HTTP Shodan query limits.
            max_results = scan_options.get("max_shodan_results")
            if max_results is not None:
                (config_overrides
                 .setdefault("http", {})
                 .setdefault("shodan", {})
                 .setdefault("query_limits", {})
                 )["max_results"] = max_results

            # HTTP discovery concurrency.
            disc_conc = scan_options.get("discovery_max_concurrent_hosts")
            if disc_conc is not None:
                config_overrides.setdefault("http", {}).setdefault("discovery", {})[
                    "max_concurrent_hosts"
                ] = disc_conc

            # HTTP access concurrency.
            acc_conc = scan_options.get("access_max_concurrent_hosts")
            if acc_conc is not None:
                config_overrides.setdefault("http", {}).setdefault("access", {})[
                    "max_concurrent_hosts"
                ] = acc_conc

            # HTTP verification timeouts (no auth_timeout — HTTP has no auth step).
            verif_overrides = {}
            for key in ("connect_timeout", "request_timeout", "subdir_timeout"):
                val = scan_options.get(key)
                if val is not None:
                    verif_overrides[key] = val
            if verif_overrides:
                config_overrides.setdefault("http", {})["verification"] = verif_overrides

            # TLS / verification flags (pass-through; no behavior in Card 2).
            for key in ("verify_http", "verify_https", "allow_insecure_tls"):
                val = scan_options.get(key)
                if val is not None:
                    config_overrides.setdefault("http", {}).setdefault("verification", {})[key] = val

            # Bulk probe (pass-through only; no behavior in Card 2).
            bulk = scan_options.get("bulk_probe_enabled")
            if bulk is not None:
                config_overrides.setdefault("http", {})["bulk_probe_enabled"] = bulk

            verbose = bool(scan_options.get("verbose", False))
            custom_filters = scan_options.get("custom_filters", "")

            if config_overrides:
                self._update_progress(7, "Applying configuration overrides...", "initialization")
                with self.backend_interface._temporary_config_override(config_overrides):
                    result = self.backend_interface.run_http_scan(
                        countries=countries,
                        progress_callback=self._handle_backend_progress,
                        log_callback=self._handle_backend_log_line,
                        filters=custom_filters,
                        verbose=verbose,
                    )
            else:
                result = self.backend_interface.run_http_scan(
                    countries=countries,
                    progress_callback=self._handle_backend_progress,
                    log_callback=self._handle_backend_log_line,
                    filters=custom_filters,
                    verbose=verbose,
                )

            self._process_scan_results(result)

        except Exception as exc:
            self._handle_scan_error(exc)

        finally:
            self._cleanup_scan()
