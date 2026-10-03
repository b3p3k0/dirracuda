"""Temporary app data and inert external services, installed before GUI imports."""
import json
import sqlite3
import socket
import subprocess
import threading
import webbrowser
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class Settings:
    def __init__(self, paths):
        self.paths = paths
        self.values = {"backend.config_path": str(paths.config_file),
                       "database.path": str(paths.main_db_file),
                       "experimental.warning_dismissed": False}
    def get_setting(self, key, default=None):
        return self.values.get(key, default)
    def set_setting(self, key, value):
        self.values[key] = value
        return True
    def get_window_setting(self, window, key, default=None):
        return self.get_setting(f"windows.{window}.{key}", default)
    def set_window_setting(self, window, key, value):
        return self.set_setting(f"windows.{window}.{key}", value)
    def get_database_path(self):
        return str(self.paths.main_db_file)
    def get_backend_path(self):
        return str(self.paths.repo_root)
    def get_theme(self):
        return self.get_setting("ui.theme", "light")
    def save_settings(self):
        return True


class Environment(ExitStack):
    def __init__(self, directory):
        super().__init__()
        from shared import path_service
        self.paths = path_service.get_paths(home_root=directory / "data")
        for name, path in vars(self.paths).items():
            if name.endswith("_dir"):
                path.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(json.dumps({
            "database": {"path": str(self.paths.main_db_file)},
            "shodan": {"api_key": ""}, "discovery": {"max_concurrent_hosts": 10},
            "connection": {"timeout": 10}, "http": {"allow_insecure_tls": True},
        }))
        with sqlite3.connect(self.paths.main_db_file) as db:
            db.executescript((self.paths.repo_root / "tools/db_schema.sql").read_text())
        original_paths = path_service.get_paths
        def fixture_paths(**kwargs):
            kwargs.setdefault("home_root", self.paths.home_root)
            return original_paths(**kwargs)
        self.enter_context(patch.object(path_service, "get_paths", fixture_paths))
        from gui.utils import settings_manager
        self.settings = Settings(self.paths)
        self.enter_context(patch.object(settings_manager, "get_settings_manager", lambda: self.settings))
        from gui.utils.background_windows import _x11
        _x11()  # Resolve the system library before blocking subprocess launches.
        self.attempts = []
        def blocked(*args, **kwargs):
            # Popen.__init__ receives an uninitialized instance whose repr is
            # itself invalid. Record types without invoking arbitrary reprs.
            self.attempts.append([type(arg).__name__ for arg in args])
            raise RuntimeError("External operation blocked by dialog audit")
        self.enter_context(patch.object(socket.socket, "connect", blocked))
        self.enter_context(patch.object(socket, "create_connection", blocked))
        self.enter_context(patch.object(subprocess.Popen, "__init__", blocked))
        self.enter_context(patch.object(webbrowser, "open", blocked))
        # Builders populate deterministic data instead of starting network/worker jobs.
        self.enter_context(patch.object(threading.Thread, "start", lambda _self: None))
        from tkinter import simpledialog
        self.enter_context(patch.object(simpledialog, "askstring", return_value=None))
        from gui.utils import safe_messagebox
        self.enter_context(patch.object(safe_messagebox, "_call", blocked))
        self.root = None
    def patch(self, target, **kwargs):
        return self.enter_context(patch(target, **kwargs))
    def context(self):
        return {"settings_manager": self.settings, "config_path": str(self.paths.config_file),
                "db_path": self.paths.main_db_file, "parent": self.root}
    @property
    def config(self):
        return str(self.paths.config_file)
    @property
    def db(self):
        from gui.utils.database_access import DatabaseReader
        return DatabaseReader(str(self.paths.main_db_file))
    @property
    def dash(self):
        from gui.utils.style import get_theme
        return SimpleNamespace(parent=self.root, theme=get_theme(), settings_manager=self.settings,
                               db_reader=self.db, config_path=self.config)
