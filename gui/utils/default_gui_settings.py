"""
Default GUI settings for Dirracuda.

Separated from SettingsManager to keep the manager implementation concise.
"""

from datetime import datetime

# Default settings dictionary used by SettingsManager
DEFAULT_GUI_SETTINGS = {
    'interface': {
        'mode': 'simple',  # 'simple' or 'advanced'
        'theme': 'light',  # 'light' or 'dark' (future)
        'auto_refresh': True,
        'confirm_exits': True
    },
    'windows': {
        'main_window': {
            'geometry': '1200x745',
            'position': 'center'
        },
        'server_list': {
            'mode': 'simple',
            'last_filters': {},
            'column_widths': {}
        },
        'vulnerability_report': {
            'mode': 'simple',
            'last_filters': {},
            'column_widths': {}
        },
        'config_editor': {
            'mode': 'simple',
            'last_section': 'scanning'
        }
    },
    'data': {
        'last_export_location': '',
        'last_import_location': '',
        'export_format_preference': 'csv',
        'import_mode_preference': 'merge',
        'favorite_servers': [],
        'avoid_servers': []
    },
    'scan_dialog': {
        'max_shodan_results': 1000,
        'recent_hours': None,  # None means use config default
        'country_code': '',
        'rescan_all': False,
        'rescan_failed': False,
        'api_key_override': '',
        'discovery_max_concurrency': 1,
        'access_max_concurrency': 1,
        'rate_limit_delay': 1,
        'share_access_delay': 1,
        'remember_api_key': False,
        'rce_enabled': False,
        'verbose': False
    },
    'unified_scan_dialog': {
        'protocol_smb': True,
        'protocol_ftp': True,
        'protocol_http': True,
        'max_shodan_results': 1000,
        'custom_filters': '',
        'country_code': '',
        'shared_concurrency': 10,
        'shared_timeout_seconds': 10,
        'verbose': False,
        'bulk_probe_enabled': False,
        'bulk_extract_enabled': False,
        'bulk_extract_skip_indicators': True,
        'rce_enabled': False,
        'security_mode': 'cautious',
        'allow_insecure_tls': True,
        'region_africa': False,
        'region_asia': False,
        'region_europe': False,
        'region_north_america': False,
        'region_oceania': False,
        'region_south_america': False
    },
    'ftp_scan_dialog': {
        'max_shodan_results': 1000,
        'api_key_override': '',
        'custom_filters': '',
        'country_code': '',
        'discovery_max_concurrent_hosts': 10,
        'access_max_concurrent_hosts': 4,
        'connect_timeout': 5,
        'auth_timeout': 10,
        'listing_timeout': 15,
        'verbose': False,
        'bulk_probe_enabled': False,
        'region_africa': False,
        'region_asia': False,
        'region_europe': False,
        'region_north_america': False,
        'region_oceania': False,
        'region_south_america': False
    },
    'probe': {
        'max_directories_per_share': 3,
        'max_files_per_directory': 5,
        'share_timeout_seconds': 10,
        'status_by_ip': {},
        'batch_max_workers': 3
    },
    'pry': {
        'wordlist_path': 'conf/wordlists/rockyou.txt',
        'user_as_pass': True,
        'stop_on_lockout': True,
        'verbose': False,
        'attempt_delay': 1.0,
        'max_attempts': 0
    },
    'extract': {
        'last_directory': '',
        'max_file_size_mb': 50,
        'max_total_size_mb': 200,
        'max_time_seconds': 300,
        'max_files_per_target': 10,
        'batch_max_workers': 2,
        'extension_mode': 'allow_only'
    },
    'file_browser': {
        'folder_limits': {}
    },
    'templates': {
        'last_used': None
    },
    'backend': {
        'mock_mode': False,
        'backend_path': '.',
        'config_path': './conf/config.json',
        'database_path': './dirracuda.db',
        'last_database_path': '',
        'database_validated': False
    },
    'metadata': {
        'version': '1.0.0',
        'created': datetime.now().isoformat(),
        'last_updated': datetime.now().isoformat()
    }
}
