"""
Pry dialog for collecting weak-password audit inputs.
"""

import json
import os
import webbrowser
import tkinter as tk
from tkinter import filedialog, ttk
from gui.utils import safe_messagebox as messagebox
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.wordlist_path import normalize_wordlist_path


class PryDialog:
    """Modal dialog to collect username, wordlist, and Pry options."""

    def __init__(self, parent: tk.Toplevel, theme, settings_manager, config_path: Optional[str], target_label: str = "", shares: Optional[Sequence[Dict[str, Any]]] = None):
        self.parent = parent
        self.theme = theme
        self.settings = settings_manager
        self.config_path = Path(config_path) if config_path else None
        self.target_label = target_label
        self.shares = list(shares or [])
        self.dialog: Optional[tk.Toplevel] = None
        self.result: Optional[Dict[str, Any]] = None

        self.defaults = self._load_defaults()

        self.username_var = tk.StringVar()
        self.wordlist_var = tk.StringVar(value=self.defaults["wordlist_path"])
        self.share_var = tk.StringVar()
        self.user_as_pass_var = tk.BooleanVar(value=self.defaults["user_as_pass"])
        self.stop_on_lockout_var = tk.BooleanVar(value=self.defaults["stop_on_lockout"])
        self.verbose_var = tk.BooleanVar(value=self.defaults["verbose"])
        self.delay_var = tk.DoubleVar(value=self.defaults["attempt_delay"])
        self.max_attempts_var = tk.IntVar(value=self.defaults["max_attempts"])

    def show(self) -> Optional[Dict[str, Any]]:
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("🔓 Pry Weak Password Audit")
        self.dialog.transient(self.parent)
        self.dialog.grab_set()

        if self.theme:
            self.theme.apply_to_widget(self.dialog, "main_window")

        main = tk.Frame(self.dialog)
        if self.theme:
            self.theme.apply_to_widget(main, "main_window")
        main.pack(padx=18, pady=16, fill=tk.BOTH, expand=True)

        row = 0
        if self.target_label:
            tk.Label(main, text=f"Target Host: {self.target_label}").grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 10))
            row += 1

        tk.Label(main, text="Share to test:").grid(row=row, column=0, sticky="w", pady=5)
        share_choices = self._build_share_choices()
        share_menu = ttk.Combobox(main, textvariable=self.share_var, values=share_choices, state="readonly" if share_choices else "disabled", width=32)
        share_menu.grid(row=row, column=1, columnspan=2, sticky="we", pady=5)
        row += 1

        tk.Label(main, text="Username to test:").grid(row=row, column=0, sticky="w", pady=5)
        tk.Entry(main, textvariable=self.username_var, width=32).grid(row=row, column=1, columnspan=2, sticky="we", pady=5)
        row += 1

        tk.Label(main, text="Password wordlist:").grid(row=row, column=0, sticky="w", pady=5)
        path_frame = tk.Frame(main)
        path_frame.grid(row=row, column=1, columnspan=2, sticky="we", pady=5)
        tk.Entry(path_frame, textvariable=self.wordlist_var, width=36).pack(side=tk.LEFT, fill=tk.X, expand=True)
        browse_btn = tk.Button(path_frame, text="Browse…", command=self._browse_wordlist)
        if self.theme:
            self.theme.apply_to_widget(browse_btn, "button_secondary")
        browse_btn.pack(side=tk.LEFT, padx=(6, 0))
        row += 1

        link = tk.Label(
            main,
            text="Need a list? SecLists",
            fg=self.theme.colors["accent"] if self.theme else "#0066cc",
            cursor="hand2"
        )
        link.grid(row=row, column=1, columnspan=2, sticky="w", pady=(0, 8))
        link.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/danielmiessler/SecLists"))
        row += 1

        tk.Checkbutton(main, text="Try username as password", variable=self.user_as_pass_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1
        tk.Checkbutton(main, text="Stop on account lockout", variable=self.stop_on_lockout_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1
        tk.Checkbutton(main, text="Verbose output (show failures)", variable=self.verbose_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        row += 1

        tk.Label(main, text="Delay between attempts (seconds):").grid(row=row, column=0, sticky="w", pady=(8, 2))
        tk.Entry(main, textvariable=self.delay_var, width=12).grid(row=row, column=1, sticky="w", pady=(8, 2))
        row += 1

        tk.Label(main, text="Max attempts (0 = unlimited):").grid(row=row, column=0, sticky="w", pady=2)
        tk.Entry(main, textvariable=self.max_attempts_var, width=12).grid(row=row, column=1, sticky="w", pady=2)
        row += 1

        button_frame = tk.Frame(main)
        button_frame.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        cancel_btn = tk.Button(button_frame, text="Cancel", command=self._on_cancel)
        start_btn = tk.Button(button_frame, text="Start", command=self._on_start)
        if self.theme:
            self.theme.apply_to_widget(cancel_btn, "button_secondary")
            self.theme.apply_to_widget(start_btn, "button_primary")
        cancel_btn.pack(side=tk.RIGHT, padx=(0, 6))
        start_btn.pack(side=tk.RIGHT)

        self.dialog.columnconfigure(1, weight=1)
        main.columnconfigure(1, weight=1)

        self.dialog.protocol("WM_DELETE_WINDOW", self._on_cancel)
        if self.theme:
            self.theme.apply_theme_to_application(self.dialog)
        ensure_dialog_focus(self.dialog, self.parent)
        self.parent.wait_window(self.dialog)
        return self.result

    # -- Internal helpers -------------------------------------------------

    def _browse_wordlist(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.dialog,
            title="Select password wordlist",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.wordlist_var.set(path)

    def _on_cancel(self) -> None:
        self.result = None
        if self.dialog:
            self.dialog.destroy()

    def _on_start(self) -> None:
        username = self.username_var.get().strip()
        wordlist = self.wordlist_var.get().strip()
        share_name = self.share_var.get().strip()
        if not share_name:
            messagebox.showerror("Missing share", "Select a share to test.", parent=self.dialog)
            return
        if not username:
            messagebox.showerror("Missing username", "Please enter a username to test.", parent=self.dialog)
            return
        if not wordlist:
            messagebox.showerror("Missing wordlist", "Select a password wordlist file.", parent=self.dialog)
            return
        path_obj = Path(wordlist)
        if wordlist.lower().endswith(".gz"):
            messagebox.showerror("Unsupported format", "gzip wordlists are not supported yet—please decompress first.", parent=self.dialog)
            return
        if not path_obj.exists() or not path_obj.is_file():
            messagebox.showerror("File not found", "The selected wordlist file cannot be found.", parent=self.dialog)
            return
        if not os_access_readable(path_obj):
            messagebox.showerror("Unreadable file", "The selected wordlist file is not readable.", parent=self.dialog)
            return

        try:
            delay = float(self.delay_var.get())
            if delay < 0:
                raise ValueError()
        except Exception:
            messagebox.showerror("Invalid delay", "Delay between attempts must be a non-negative number.", parent=self.dialog)
            return

        try:
            max_attempts = int(self.max_attempts_var.get())
            if max_attempts < 0:
                raise ValueError()
        except Exception:
            messagebox.showerror("Invalid max attempts", "Max attempts must be zero or a positive integer.", parent=self.dialog)
            return

        # Persist defaults
        if self.settings:
            try:
                self.settings.set_setting('pry.wordlist_path', str(path_obj))
                self.settings.set_setting('pry.user_as_pass', bool(self.user_as_pass_var.get()))
                self.settings.set_setting('pry.stop_on_lockout', bool(self.stop_on_lockout_var.get()))
                self.settings.set_setting('pry.verbose', bool(self.verbose_var.get()))
                self.settings.set_setting('pry.attempt_delay', float(delay))
                self.settings.set_setting('pry.max_attempts', int(max_attempts))
            except Exception:
                pass

        self.result = {
            "username": username,
            "share_name": share_name,
            "wordlist_path": str(path_obj),
            "options": {
                "user_as_pass": bool(self.user_as_pass_var.get()),
                "stop_on_lockout": bool(self.stop_on_lockout_var.get()),
                "verbose": bool(self.verbose_var.get()),
                "attempt_delay": float(delay),
                "max_attempts": int(max_attempts),
            }
        }
        if self.dialog:
            self.dialog.destroy()

    def _load_defaults(self) -> Dict[str, Any]:
        defaults = {
            "wordlist_path": "",
            "user_as_pass": True,
            "stop_on_lockout": True,
            "verbose": False,
            "attempt_delay": 1.0,
            "max_attempts": 0,
        }
        # From settings manager
        if self.settings:
            try:
                defaults["wordlist_path"] = self.settings.get_setting('pry.wordlist_path', defaults["wordlist_path"])
                defaults["user_as_pass"] = bool(self.settings.get_setting('pry.user_as_pass', defaults["user_as_pass"]))
                defaults["stop_on_lockout"] = bool(self.settings.get_setting('pry.stop_on_lockout', defaults["stop_on_lockout"]))
                defaults["verbose"] = bool(self.settings.get_setting('pry.verbose', defaults["verbose"]))
                defaults["attempt_delay"] = float(self.settings.get_setting('pry.attempt_delay', defaults["attempt_delay"]))
                defaults["max_attempts"] = int(self.settings.get_setting('pry.max_attempts', defaults["max_attempts"]))
            except Exception:
                pass

        # From config file
        if self.config_path and self.config_path.exists():
            try:
                cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                pry_cfg = cfg.get("pry", {})
                defaults["wordlist_path"] = pry_cfg.get("wordlist_path", defaults["wordlist_path"])
                defaults["user_as_pass"] = bool(pry_cfg.get("user_as_pass", defaults["user_as_pass"]))
                defaults["stop_on_lockout"] = bool(pry_cfg.get("stop_on_lockout", defaults["stop_on_lockout"]))
                defaults["verbose"] = bool(pry_cfg.get("verbose", defaults["verbose"]))
                defaults["attempt_delay"] = float(pry_cfg.get("attempt_delay", defaults["attempt_delay"]))
                defaults["max_attempts"] = int(pry_cfg.get("max_attempts", defaults["max_attempts"]))
            except Exception:
                pass

        defaults["wordlist_path"] = normalize_wordlist_path(
            defaults.get("wordlist_path", ""),
            config_path=self.config_path,
        )

        return defaults

    def _build_share_choices(self) -> Sequence[str]:
        if not self.shares:
            self.share_var.set("")
            return []

        # Sort denied first (accessible False), then accessible
        denied = [s for s in self.shares if not s.get("accessible")]
        allowed = [s for s in self.shares if s.get("accessible")]
        ordered = denied + allowed

        names = []
        seen = set()
        for entry in ordered:
            name = entry.get("share_name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            badge = "(denied)" if not entry.get("accessible") else "(accessible)"
            names.append(f"{name} {badge}")

        if names:
            # Preselect first denied, else first item
            self.share_var.set(names[0])
        else:
            self.share_var.set("")
        return names


def os_access_readable(path: Path) -> bool:
    """Return True if file is readable by current process."""
    try:
        return path.exists() and path.is_file() and os.access(path, os.R_OK)
    except Exception:
        return False


__all__ = ["PryDialog"]
