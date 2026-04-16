"""
Dirracuda Mission Control Dashboard — compatibility shim (C9).

DashboardWidget implementation lives in gui.dashboard.widget.
This module re-exports DashboardWidget and keeps all patch-sensitive names
bound at module scope so frozen test patch paths (gui.components.dashboard.*)
remain valid.
"""

import tkinter as tk
from tkinter import ttk
import webbrowser
import tkinter.font as tkfont
from gui.utils import safe_messagebox as messagebox
import threading
import time
import json
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
import sys
import os
import queue
from collections import deque
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from gui.utils.database_access import DatabaseReader
from gui.utils.backend_interface import BackendInterface
from gui.utils.style import get_theme, apply_theme_to_window
from gui.utils.scan_manager import get_scan_manager
from gui.components.unified_scan_dialog import show_unified_scan_dialog
from gui.components.ftp_scan_dialog import show_ftp_scan_dialog
from gui.components.http_scan_dialog import show_http_scan_dialog
from gui.components.reddit_grab_dialog import show_reddit_grab_dialog
from experimental.redseek.service import IngestOptions, IngestResult, run_ingest
from gui.components.scan_results_dialog import show_scan_results_dialog
from gui.components.batch_summary_dialog import show_batch_summary_dialog
from gui.utils.settings_manager import get_settings_manager
from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.components import dashboard_logs
from gui.components import dashboard_status
from gui.components import dashboard_scan
from gui.components import dashboard_batch_ops
from gui.utils import (
    probe_cache,
    probe_patterns,
    extract_runner,
)
from gui.utils.probe_cache_dispatch import get_probe_snapshot_path_for_host, dispatch_probe_run
from gui.utils.probe_snapshot_summary import summarize_probe_snapshot
from gui.utils.logging_config import get_logger
from shared.quarantine import create_quarantine_dir
from shared.tmpfs_quarantine import get_tmpfs_runtime_state

_logger = get_logger("dashboard")

from gui.dashboard.widget import DashboardWidget  # noqa: E402
