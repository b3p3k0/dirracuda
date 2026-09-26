"""Egress consent for a remote Analyst run (contract 10).

A remote run sends extracted file text off this machine. That text is
harvested from open directories and is frequently sensitive, so the operator
confirms it -- by default, every time.

Muting hides the dialog, never the fact: the run view keeps a persistent
``Remote: <profile>`` marker regardless.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.session_flags import (
    ANALYST_REMOTE_EGRESS_MUTE_KEY,
    get_flag,
    set_flag,
)
from gui.utils.style import get_theme


def _mb():
    """Resolve safe_messagebox through analyst_tab, for test monkeypatches."""
    from gui.components.experimental_features import analyst_tab

    return analyst_tab.safe_messagebox


def remote_marker(profile) -> str:
    """Return the persistent marker a remote run always shows.

    Contract 10.4: muting the dialog must never hide that a run is remote.
    """
    if profile is None or profile.is_loopback:
        return ""
    return f"Remote: {profile.name}"


def consent_required(profile) -> bool:
    """Return whether this run must ask before sending anything.

    A loopback profile never asks. Otherwise the dialog is shown unless it was
    muted for this session or muted forever on this profile -- and a profile
    added later brings it back, which is deliberate.
    """
    if profile is None or profile.is_loopback:
        return False
    if bool(getattr(profile, "consent_muted", False)):
        return False
    return not get_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY)


class _ConsentDialog:
    """Modal confirmation naming the profile, host, model and what is sent."""

    def __init__(self, parent, *, profile, model_name: str) -> None:
        self.allowed = False
        self.mute_session = False
        self.mute_forever = False
        self._theme = get_theme()
        self._dialog = tk.Toplevel(parent)
        self._dialog.title("Analyst — Send to a remote server?")
        self._dialog.transient(parent)
        self._dialog.resizable(False, False)
        self._theme.apply_to_widget(self._dialog, "main_window")
        self._session_var = tk.BooleanVar(value=False)
        self._forever_var = tk.BooleanVar(value=False)
        self._build(profile, model_name)
        self._dialog.grab_set()
        self._dialog.protocol("WM_DELETE_WINDOW", self._refuse)
        ensure_dialog_focus(self._dialog, parent)
        parent.wait_window(self._dialog)

    def _build(self, profile, model_name: str) -> None:
        outer = tk.Frame(self._dialog, padx=16, pady=14)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)

        heading = tk.Label(
            outer, anchor="w", justify=tk.LEFT,
            text="This run sends file text to another machine.",
        )
        self._theme.apply_to_widget(heading, "label")
        heading.pack(fill=tk.X)

        detail = tk.Label(
            outer, anchor="w", justify=tk.LEFT, wraplength=460,
            text=(
                f"Server : {profile.name} ({profile.endpoint_url})\n"
                f"Model  : {model_name}\n\n"
                "Analyst will send the extracted text of every file it reads "
                "to that server. That text comes from open directories and is "
                "often sensitive. Analyst cannot prove where the server sends "
                "it afterwards."
            ),
        )
        self._theme.apply_to_widget(detail, "label")
        detail.pack(fill=tk.X, pady=(8, 10))

        session = tk.Checkbutton(
            outer, text="Do not ask again this session",
            variable=self._session_var,
        )
        self._theme.apply_to_widget(session, "checkbox")
        session.pack(anchor="w")

        forever = tk.Checkbutton(
            outer, text=f"Do not ask again for '{profile.name}'",
            variable=self._forever_var,
        )
        self._theme.apply_to_widget(forever, "checkbox")
        forever.pack(anchor="w", pady=(0, 10))

        buttons = tk.Frame(outer)
        self._theme.apply_to_widget(buttons, "main_window")
        buttons.pack(anchor="e")
        send = tk.Button(buttons, text="Send", command=self._allow)
        self._theme.apply_to_widget(send, "button_primary")
        send.pack(side=tk.LEFT, padx=(0, 7))
        cancel = tk.Button(buttons, text="Cancel", command=self._refuse)
        self._theme.apply_to_widget(cancel, "button_secondary")
        cancel.pack(side=tk.LEFT)

    def _allow(self) -> None:
        self.allowed = True
        self.mute_session = bool(self._session_var.get())
        self.mute_forever = bool(self._forever_var.get())
        self._close()

    def _refuse(self) -> None:
        self.allowed = False
        self._close()

    def _close(self) -> None:
        for step in (self._dialog.grab_release, self._dialog.destroy):
            try:
                step()
            except Exception:
                pass


def confirm_remote_egress(
    parent, *, profile, model_name: str, db_path: Any = None,
) -> bool:
    """Ask before a remote run, and honour the answer. Returns whether to go.

    "Mute this session" resets on restart. "Mute forever" is per profile, so a
    profile added later asks again -- which is the point: consenting to one
    host is not consenting to the next.
    """
    if not consent_required(profile):
        return True
    dialog = _ConsentDialog(parent, profile=profile, model_name=model_name)
    if not dialog.allowed:
        return False
    if dialog.mute_session:
        set_flag(ANALYST_REMOTE_EGRESS_MUTE_KEY, True)
    if dialog.mute_forever:
        from experimental.analyst.profiles import update_profile

        try:
            update_profile(profile.profile_id, consent_muted=True, path=db_path)
        except Exception as exc:
            _mb().showwarning(
                "Analyst",
                f"The run will continue, but that preference was not saved.\n\n{exc}",
                parent=parent,
            )
    return True


__all__ = [
    "confirm_remote_egress",
    "consent_required",
    "remote_marker",
]
