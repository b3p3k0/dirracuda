"""
Dirracuda - Settings Manager

Global settings management for Dirracuda including user preferences,
interface modes, and persistent configuration storage.

Design Decision: Centralized settings management allows consistent behavior
across all application components and provides user preference persistence.
"""

import json
import os
import copy
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List
from datetime import datetime

from gui.utils.default_gui_settings import DEFAULT_GUI_SETTINGS
from gui.utils.logging_config import get_logger

_logger = get_logger("settings_manager")


class SettingsManager:
    """
    Global settings manager for Dirracuda.
    
    Handles user preferences, interface modes, window positions,
    and other persistent application settings.
    """
    
    def __init__(self, settings_dir: Optional[str] = None):
        """
        Initialize settings manager.
        
        Args:
            settings_dir: Directory to store settings files (default: ~/.smbseek)
        """
        # Default settings directory
        if settings_dir is None:
            home_dir = Path.home()
            self.settings_dir = home_dir / '.smbseek'
        else:
            self.settings_dir = Path(settings_dir)
        
        self.settings_file = self.settings_dir / 'gui_settings.json'
        
        # Ensure settings directory exists
        self.settings_dir.mkdir(exist_ok=True)
        
        # Default settings (deep copy so nested values are not shared/mutated globally)
        self.default_settings = copy.deepcopy(DEFAULT_GUI_SETTINGS)
        
        # Current settings (loaded from file or defaults)
        self.settings = {}
        self._settings_lock = threading.RLock()
        self._change_callbacks = []
        
        # Load settings from file
        self.load_settings()
        self._auto_fix_backend_paths()
    
    def load_settings(self) -> None:
        """Load settings from file or create defaults."""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    file_settings = json.load(f)
                
                # Merge with defaults (in case new settings were added)
                merged = self._merge_settings(self.default_settings, file_settings)
                
                # Migrate legacy settings to new format
                migrated = self._migrate_legacy_settings(merged)
                
                # Update last_updated timestamp
                migrated.setdefault('metadata', {})
                migrated['metadata']['last_updated'] = datetime.now().isoformat()
                with self._settings_lock:
                    self.settings = migrated
                
            else:
                # Use defaults and save them
                with self._settings_lock:
                    self.settings = copy.deepcopy(self.default_settings)
                self.save_settings()
                
        except Exception as e:
            _logger.warning("Failed to load settings: %s", e)
            _logger.warning("Using default settings")
            with self._settings_lock:
                self.settings = copy.deepcopy(self.default_settings)
    
    def save_settings(self) -> bool:
        """
        Save current settings to file.
        
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            with self._settings_lock:
                self.settings.setdefault('metadata', {})
                self.settings['metadata']['last_updated'] = datetime.now().isoformat()
                # Serialize a stable snapshot so concurrent mutations cannot break json.dump
                settings_snapshot = copy.deepcopy(self.settings)
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings_snapshot, f, indent=2)
            
            return True
            
        except Exception as e:
            _logger.error("Failed to save settings: %s", e)
            return False
    
    def get_setting(self, key_path: str, default: Any = None) -> Any:
        """
        Get setting value by dot-separated key path.
        
        Args:
            key_path: Dot-separated path like 'interface.mode' or 'windows.main_window.geometry'
            default: Default value if key not found
            
        Returns:
            Setting value or default
        """
        keys = key_path.split('.')
        with self._settings_lock:
            current = self.settings
            try:
                for key in keys:
                    current = current[key]
                # Return copies for mutable objects to avoid external in-place mutation.
                if isinstance(current, (dict, list)):
                    return copy.deepcopy(current)
                return current
            except (KeyError, TypeError):
                return default
    
    def set_setting(self, key_path: str, value: Any, save_immediately: bool = True) -> bool:
        """
        Set setting value by dot-separated key path.
        
        Args:
            key_path: Dot-separated path like 'interface.mode'
            value: Value to set
            save_immediately: Whether to save to file immediately
            
        Returns:
            True if set successfully, False otherwise
        """
        keys = key_path.split('.')
        try:
            with self._settings_lock:
                current = self.settings

                # Navigate to parent of target key
                for key in keys[:-1]:
                    if key not in current or not isinstance(current[key], dict):
                        current[key] = {}
                    current = current[key]

                # Set the final key
                old_value = current.get(keys[-1])
                stored_value = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
                current[keys[-1]] = stored_value

            # Notify callbacks if value changed
            if old_value != value:
                self._notify_change_callbacks(key_path, old_value, value)

            # Save if requested
            if save_immediately:
                return self.save_settings()

            return True
            
        except Exception as e:
            _logger.error("Failed to set setting %s: %s", key_path, e)
            return False
    
    def _merge_settings(self, defaults: Dict[str, Any], user_settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge user settings with defaults, preserving structure.
        
        Args:
            defaults: Default settings structure
            user_settings: User settings to merge
            
        Returns:
            Merged settings dictionary
        """
        result = defaults.copy()
        
        for key, value in user_settings.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_settings(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _migrate_legacy_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate legacy settings to new format.
        
        Updates old geometry settings to use new compact dimensions.
        This ensures existing settings files get the new 350px height.
        """
        # Update legacy main window geometry settings
        legacy_geometries = [
            '800x700',
            '1200x800',
            '800x550',
            '800x750',
            '800x350',
            '900x250'
        ]
        current_geometry = settings.get('windows', {}).get('main_window', {}).get('geometry')
        
        if current_geometry in legacy_geometries:
            settings['windows']['main_window']['geometry'] = '1200x745'
            
        return settings
    
    def get_interface_mode(self) -> str:
        """Get current interface mode (simple/advanced)."""
        return self.get_setting('interface.mode', 'simple')
    
    def set_interface_mode(self, mode: str) -> bool:
        """
        Set interface mode globally.
        
        Args:
            mode: 'simple' or 'advanced'
            
        Returns:
            True if set successfully
        """
        if mode not in ['simple', 'advanced']:
            raise ValueError("Mode must be 'simple' or 'advanced'")
        
        return self.set_setting('interface.mode', mode)
    
    def toggle_interface_mode(self) -> str:
        """
        Toggle between simple and advanced modes.
        
        Returns:
            New mode after toggle
        """
        current_mode = self.get_interface_mode()
        new_mode = 'advanced' if current_mode == 'simple' else 'simple'
        self.set_interface_mode(new_mode)
        return new_mode
    
    def get_window_setting(self, window_name: str, setting_name: str, default: Any = None) -> Any:
        """
        Get window-specific setting.
        
        Args:
            window_name: Name of the window
            setting_name: Name of the setting
            default: Default value if not found
            
        Returns:
            Setting value or default
        """
        return self.get_setting(f'windows.{window_name}.{setting_name}', default)
    
    def set_window_setting(self, window_name: str, setting_name: str, value: Any) -> bool:
        """
        Set window-specific setting.
        
        Args:
            window_name: Name of the window
            setting_name: Name of the setting
            value: Value to set
            
        Returns:
            True if set successfully
        """
        return self.set_setting(f'windows.{window_name}.{setting_name}', value)
    
    def get_window_mode(self, window_name: str) -> str:
        """
        Get mode for specific window.
        
        Args:
            window_name: Name of the window
            
        Returns:
            Window mode ('simple' or 'advanced'), defaults to global mode
        """
        # Try window-specific mode first, then fall back to global mode
        window_mode = self.get_window_setting(window_name, 'mode')
        if window_mode:
            return window_mode
        else:
            return self.get_interface_mode()
    
    def set_window_mode(self, window_name: str, mode: str) -> bool:
        """
        Set mode for specific window.
        
        Args:
            window_name: Name of the window
            mode: 'simple' or 'advanced'
            
        Returns:
            True if set successfully
        """
        if mode not in ['simple', 'advanced']:
            raise ValueError("Mode must be 'simple' or 'advanced'")
        
        return self.set_window_setting(window_name, 'mode', mode)
    
    def reset_to_defaults(self, section: Optional[str] = None) -> bool:
        """
        Reset settings to defaults.
        
        Args:
            section: Optional section to reset (e.g., 'interface', 'windows')
                    If None, resets all settings
            
        Returns:
            True if reset successfully
        """
        try:
            with self._settings_lock:
                if section:
                    if section in self.default_settings:
                        self.settings[section] = copy.deepcopy(self.default_settings[section])
                    else:
                        return False
                else:
                    self.settings = copy.deepcopy(self.default_settings)
            
            return self.save_settings()
            
        except Exception as e:
            _logger.error("Failed to reset settings: %s", e)
            return False
    
    def export_settings(self, export_path: str) -> bool:
        """
        Export settings to file.
        
        Args:
            export_path: Path to export settings to
            
        Returns:
            True if exported successfully
        """
        try:
            with self._settings_lock:
                settings_snapshot = copy.deepcopy(self.settings)
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(settings_snapshot, f, indent=2)
            return True
            
        except Exception as e:
            _logger.error("Failed to export settings: %s", e)
            return False
    
    def import_settings(self, import_path: str, merge: bool = True) -> bool:
        """
        Import settings from file.
        
        Args:
            import_path: Path to import settings from
            merge: Whether to merge with current settings or replace entirely
            
        Returns:
            True if imported successfully
        """
        try:
            with open(import_path, 'r', encoding='utf-8') as f:
                imported_settings = json.load(f)
            
            with self._settings_lock:
                if merge:
                    self.settings = self._merge_settings(self.settings, imported_settings)
                else:
                    # Validate imported settings have required structure
                    if 'metadata' not in imported_settings:
                        imported_settings['metadata'] = copy.deepcopy(
                            self.default_settings.get('metadata', {})
                        )
                    self.settings = imported_settings
            
            return self.save_settings()
            
        except Exception as e:
            _logger.error("Failed to import settings: %s", e)
            return False
    
    def register_change_callback(self, callback: Callable[[str, Any, Any], None]) -> None:
        """
        Register callback for setting changes.
        
        Args:
            callback: Function to call when settings change (key_path, old_value, new_value)
        """
        with self._settings_lock:
            if callback not in self._change_callbacks:
                self._change_callbacks.append(callback)
    
    def unregister_change_callback(self, callback: Callable[[str, Any, Any], None]) -> None:
        """
        Unregister setting change callback.
        
        Args:
            callback: Callback function to remove
        """
        with self._settings_lock:
            if callback in self._change_callbacks:
                self._change_callbacks.remove(callback)
    
    def get_database_path(self) -> str:
        """
        Get the database path to use for the current session.
        
        Returns:
            Database path (last used if available, otherwise default)
        """
        last_db = self.get_setting('backend.last_database_path', '')
        if last_db and os.path.exists(last_db):
            return last_db
        else:
            return self.get_setting('backend.database_path', '../backend/smbseek.db')
    
    def set_database_path(self, db_path: str, validate: bool = True) -> bool:
        """
        Set the current database path and optionally validate it.
        
        Args:
            db_path: Path to database file
            validate: Whether to validate the database file exists
            
        Returns:
            True if set successfully (and validated if requested)
        """
        if validate and not os.path.exists(db_path):
            return False
        
        # Set both current and last database paths
        success1 = self.set_setting('backend.database_path', db_path)
        success2 = self.set_setting('backend.last_database_path', db_path)
        success3 = self.set_setting('backend.database_validated', validate)
        
        return success1 and success2 and success3
    
    def is_database_validated(self) -> bool:
        """
        Check if current database path has been validated.
        
        Returns:
            True if database was validated
        """
        return self.get_setting('backend.database_validated', False)
    
    def clear_database_validation(self) -> None:
        """Clear database validation flag (used when database becomes invalid)."""
        self.set_setting('backend.database_validated', False)
    
    def get_backend_path(self) -> str:
        """
        Get the backend path to use for backend integration.
        
        Returns:
            Backend path (default: '../backend')
        """
        return self.get_setting('backend.backend_path', '.')
    
    def set_backend_path(self, backend_path: str, validate: bool = True) -> bool:
        """
        Set the backend path for backend integration.
        
        Args:
            backend_path: Path to backend directory
            validate: Whether to validate the backend path exists
            
        Returns:
            True if set successfully (and validated if requested)
        """
        if validate and not os.path.exists(backend_path):
            return False
        
        return self.set_setting('backend.backend_path', backend_path)
    
    def _notify_change_callbacks(self, key_path: str, old_value: Any, new_value: Any) -> None:
        """
        Notify registered callbacks of setting changes.
        
        Args:
            key_path: Path of changed setting
            old_value: Previous value
            new_value: New value
        """
        with self._settings_lock:
            callbacks = list(self._change_callbacks)
        for callback in callbacks:
            try:
                callback(key_path, old_value, new_value)
            except Exception as e:
                _logger.warning("Settings callback error: %s", e)

    def _auto_fix_backend_paths(self) -> None:
        """
        Keep backend/config/database paths aligned with the active checkout.

        Behavior:
        - If stored backend/config are missing, fall back to cwd when it looks like
          a valid smbseek checkout.
        - If stored backend exists but points at a different checkout than cwd, prefer
          cwd so scans run against the currently launched repository.
        """
        try:
            backend_path = Path(self.get_backend_path()).expanduser()
            config_path = Path(self.get_setting('backend.config_path', '')).expanduser()
            db_path = Path(self.get_setting('backend.database_path', '')).expanduser()

            candidate_backend = Path.cwd()
            candidate_config = candidate_backend / "conf" / "config.json"
            candidate_db = candidate_backend / "smbseek.db"
            candidate_smbseek = candidate_backend / "cli" / "smbseek.py"
            candidate_ftpseek = candidate_backend / "cli" / "ftpseek.py"
            candidate_httpseek = candidate_backend / "cli" / "httpseek.py"

            candidate_is_checkout = (
                candidate_backend.exists()
                and candidate_config.exists()
                and candidate_smbseek.exists()
                and candidate_ftpseek.exists()
                and candidate_httpseek.exists()
            )

            # Nothing usable in cwd; keep existing settings as-is.
            if not candidate_is_checkout:
                return

            backend_exists = backend_path.exists()
            config_exists = config_path.exists()

            try:
                backend_resolved = backend_path.resolve() if backend_exists else None
            except Exception:
                backend_resolved = None
            try:
                candidate_resolved = candidate_backend.resolve()
            except Exception:
                candidate_resolved = candidate_backend

            # Missing stored paths -> repair to cwd checkout.
            if not (backend_exists and config_exists):
                self.set_backend_path(str(candidate_backend), validate=False)
                self.set_setting('backend.config_path', str(candidate_config))
                if not db_path.exists():
                    self.set_database_path(str(candidate_db), validate=False)
                return

            # Stored backend points to a different checkout; prefer cwd.
            if backend_resolved is not None and backend_resolved != candidate_resolved:
                self.set_backend_path(str(candidate_backend), validate=False)
                self.set_setting('backend.config_path', str(candidate_config))

                # Repoint DB if it is missing or still tied to old backend root.
                db_missing = not db_path.exists()
                db_under_old_backend = False
                if not db_missing:
                    try:
                        db_under_old_backend = str(db_path.resolve()).startswith(str(backend_resolved))
                    except Exception:
                        db_under_old_backend = str(db_path).startswith(str(backend_path))
                if db_missing or db_under_old_backend:
                    self.set_database_path(str(candidate_db), validate=False)
        except Exception:
            # Fail silently; worst case the user corrects via dialog
            pass
    
    def validate_smbseek_installation(self, path: str) -> Dict[str, Any]:
        """
        Validate SMBSeek installation at given path.
        
        Args:
            path: Path to SMBSeek installation directory
            
        Returns:
            Validation result with 'valid' bool and 'message' str
        """
        import subprocess
        
        if not path:
            return {'valid': False, 'message': 'Path is required'}
        
        try:
            path_obj = Path(path)
            
            if not path_obj.exists():
                return {'valid': False, 'message': 'Path does not exist'}
            
            if not path_obj.is_dir():
                return {'valid': False, 'message': 'Path is not a directory'}
            
            # Check for smbseek executable
            smbseek_script = path_obj / "cli" / "smbseek.py"
            if not smbseek_script.exists():
                return {'valid': False, 'message': 'smbseek executable not found in directory'}

            # Try to get version
            try:
                result = subprocess.run(
                    [str(smbseek_script), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
                    return {'valid': True, 'message': f'Valid SMBSeek installation ({version})'}
                else:
                    return {'valid': True, 'message': 'SMBSeek installation found (version check failed)'}
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                return {'valid': True, 'message': 'SMBSeek installation found (version check failed)'}
                
        except Exception as e:
            return {'valid': False, 'message': f'Validation error: {str(e)}'}

    def get_smbseek_config_path(self) -> str:
        """
        Get the SMBSeek configuration file path based on SMBSeek installation path.
        
        Returns:
            Path to SMBSeek config.json file
        """
        smbseek_path = self.get_backend_path()
        return str(Path(smbseek_path) / "conf" / "config.json")
    
    def set_smbseek_paths(self, smbseek_path: str, config_path: Optional[str] = None, 
                         db_path: Optional[str] = None) -> bool:
        """
        Set SMBSeek-related paths atomically.
        
        Args:
            smbseek_path: Path to SMBSeek installation
            config_path: Path to config file (optional, will be derived if not provided)
            db_path: Path to database file (optional, will be derived if not provided)
            
        Returns:
            True if all paths set successfully
        """
        try:
            # Derive paths if not provided
            smbseek_pathobj = Path(smbseek_path)
            
            if config_path is None:
                config_path = str(smbseek_pathobj / "conf" / "config.json")
            
            if db_path is None:
                db_path = str(smbseek_pathobj / "smbseek.db")
            
            # Set all paths
            success1 = self.set_backend_path(smbseek_path)
            success2 = self.set_setting('backend.config_path', config_path)
            success3 = self.set_database_path(db_path)
            
            return success1 and success2 and success3
            
        except Exception as e:
            _logger.error("Error setting SMBSeek paths: %s", e)
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get settings statistics and metadata.
        
        Returns:
            Dictionary with settings statistics
        """
        def count_settings(obj, depth=0):
            if isinstance(obj, dict):
                count = 0
                for value in obj.values():
                    count += count_settings(value, depth + 1)
                return count
            else:
                return 1
        
        return {
            'total_settings': count_settings(self.settings),
            'settings_file': str(self.settings_file),
            'settings_dir': str(self.settings_dir),
            'file_exists': self.settings_file.exists(),
            'file_size': self.settings_file.stat().st_size if self.settings_file.exists() else 0,
            'created': self.get_setting('metadata.created'),
            'last_updated': self.get_setting('metadata.last_updated'),
            'version': self.get_setting('metadata.version')
        }

    def get_favorite_servers(self) -> List[str]:
        """
        Get list of favorite server IP addresses.

        Returns:
            List of IP addresses marked as favorites
        """
        return self.get_setting('data.favorite_servers', [])

    def is_favorite_server(self, ip: Optional[str]) -> bool:
        """
        Check if server IP is marked as favorite.

        Args:
            ip: IP address to check (None/empty strings return False)

        Returns:
            True if IP is in favorites list, False otherwise
        """
        if not ip or not ip.strip():
            return False

        favorites = self.get_favorite_servers()
        return ip.strip() in favorites

    def add_favorite_server(self, ip: Optional[str]) -> None:
        """
        Add server IP to favorites list.

        Args:
            ip: IP address to add (None/empty strings are ignored)
        """
        if not ip or not ip.strip():
            return

        ip = ip.strip()
        favorites = self.get_favorite_servers()

        if ip not in favorites:
            favorites.append(ip)
            self.set_setting('data.favorite_servers', favorites)

    def remove_favorite_server(self, ip: Optional[str]) -> None:
        """
        Remove server IP from favorites list.

        Args:
            ip: IP address to remove (None/empty strings are ignored)
        """
        if not ip or not ip.strip():
            return

        ip = ip.strip()
        favorites = self.get_favorite_servers()

        if ip in favorites:
            favorites.remove(ip)
            self.set_setting('data.favorite_servers', favorites)

    def toggle_favorite_server(self, ip: Optional[str]) -> bool:
        """
        Toggle favorite status of server IP.

        Args:
            ip: IP address to toggle (None/empty strings return False)

        Returns:
            True if IP is now a favorite, False otherwise
        """
        if not ip or not ip.strip():
            return False

        if self.is_favorite_server(ip):
            self.remove_favorite_server(ip)
            return False
        else:
            self.add_favorite_server(ip)
            return True

    def get_avoid_servers(self) -> List[str]:
        """
        Get list of avoid server IP addresses.

        Returns:
            List of IP addresses marked to avoid
        """
        return self.get_setting('data.avoid_servers', [])

    def is_avoid_server(self, ip: Optional[str]) -> bool:
        """
        Check if server IP is marked to avoid.

        Args:
            ip: IP address to check (None/empty strings return False)

        Returns:
            True if IP is in avoid list, False otherwise
        """
        if not ip or not ip.strip():
            return False

        avoid_list = self.get_avoid_servers()
        return ip.strip() in avoid_list

    # Template helpers -----------------------------------------------------

    def get_last_template_slug(self) -> Optional[str]:
        """Return slug/key of last-used scan template."""
        return self.get_setting('templates.last_used', None)

    def set_last_template_slug(self, slug: Optional[str]) -> None:
        """Persist slug/key for last-used scan template."""
        self.set_setting('templates.last_used', slug)

    # Probe status helpers -------------------------------------------------

    def _get_probe_status_store_locked(self) -> Dict[str, str]:
        """
        Return mutable probe status map while settings lock is held.

        Creates missing/invalid nested structures on demand.
        """
        probe_section = self.settings.get('probe')
        if not isinstance(probe_section, dict):
            probe_section = {}
            self.settings['probe'] = probe_section

        status_map = probe_section.get('status_by_ip')
        if not isinstance(status_map, dict):
            status_map = {}
            probe_section['status_by_ip'] = status_map

        return status_map

    def get_probe_status_map(self) -> Dict[str, str]:
        """Return immutable copy of probe status map (ip -> status)."""
        with self._settings_lock:
            # Return a shallow copy to prevent accidental in-place edits.
            return dict(self._get_probe_status_store_locked())

    def get_probe_status(self, ip_address: str) -> str:
        """Return stored status for an IP (defaults to 'unprobed')."""
        if not ip_address:
            return 'unprobed'
        with self._settings_lock:
            return self._get_probe_status_store_locked().get(ip_address, 'unprobed')

    def set_probe_status(self, ip_address: str, status: str) -> None:
        """Persist probe status for an IP."""
        if not ip_address:
            return
        allowed = {'unprobed', 'clean', 'issue'}
        if status not in allowed:
            status = 'unprobed'

        changed = False
        with self._settings_lock:
            status_map = self._get_probe_status_store_locked()
            if status_map.get(ip_address) != status:
                status_map[ip_address] = status
                changed = True

        if changed:
            self.save_settings()

    def add_avoid_server(self, ip: Optional[str]) -> None:
        """
        Add server IP to avoid list.

        Args:
            ip: IP address to add (None/empty strings are ignored)
        """
        if not ip or not ip.strip():
            return

        ip = ip.strip()
        avoid_list = self.get_avoid_servers()

        if ip not in avoid_list:
            avoid_list.append(ip)
            self.set_setting('data.avoid_servers', avoid_list)

    def remove_avoid_server(self, ip: Optional[str]) -> None:
        """
        Remove server IP from avoid list.

        Args:
            ip: IP address to remove (None/empty strings are ignored)
        """
        if not ip or not ip.strip():
            return

        ip = ip.strip()
        avoid_list = self.get_avoid_servers()

        if ip in avoid_list:
            avoid_list.remove(ip)
            self.set_setting('data.avoid_servers', avoid_list)

    def toggle_avoid_server(self, ip: Optional[str]) -> bool:
        """
        Toggle avoid status of server IP.

        Args:
            ip: IP address to toggle (None/empty strings return False)

        Returns:
            True if IP is now avoided, False otherwise
        """
        if not ip or not ip.strip():
            return False

        if self.is_avoid_server(ip):
            self.remove_avoid_server(ip)
            return False
        else:
            self.add_avoid_server(ip)
            return True


# Global settings manager instance
_settings_manager = None


def get_settings_manager(settings_dir: Optional[str] = None) -> SettingsManager:
    """
    Get the global settings manager instance.
    
    Args:
        settings_dir: Directory for settings (only used on first call)
        
    Returns:
        SettingsManager instance
    """
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager(settings_dir)
    return _settings_manager
