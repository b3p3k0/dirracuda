"""Analyst model-server profile editor (N1).

A satellite of ``analyst_tab`` so the tab itself does not grow a profile CRUD
UI inline.  One-way import: ``analyst_tab`` imports this module, never the
reverse.  Messageboxes route through ``_mb()`` so test monkeypatches on the
``analyst_tab`` namespace still intercept, following the dispatch discipline in
``CLAUDE.md``.

A profile is saved whatever its address, but the transport policy decides
whether it can be contacted (contract 4.1-4.3). The editor says what is still
missing rather than letting a run fail later.
"""

from __future__ import annotations

from gui.utils.scrollable_body import wrap_label
import tkinter as tk
from tkinter import ttk
from typing import Callable

from gui.utils.dialog_helpers import ensure_dialog_focus
from gui.utils.style import get_theme


REMOTE_HELD_NOTE = (
    "Saved, but not usable yet: tick 'Allow plaintext to this private address' "
    "on the profile, or use https. A public address is refused outright."
)

_ADDRESS_LABELS = {
    "loopback": "This machine",
    "private": "Private LAN",
    "cgnat": "Tailscale / CGNAT",
    "unique_local": "Private IPv6",
    "link_local": "Link-local",
    "public": "Public internet",
    "unresolved": "Hostname (not resolved)",
}


def _mb():
    """Resolve safe_messagebox through the analyst_tab namespace at call time."""
    from gui.components.experimental_features import analyst_tab

    return analyst_tab.safe_messagebox


def _profiles():
    """Import the profiles module lazily so Tk import order stays unaffected."""
    from experimental.analyst import profiles

    return profiles


def describe_profile(profile) -> str:
    """Return one list row for a profile, with its address class spelled out."""
    where = _ADDRESS_LABELS.get(profile.address_class.value, "Unknown")
    held = "" if profile.is_reachable_now else f"  — {profile.policy_note}"
    return f"{profile.name}  ({profile.endpoint_url})  [{where}]{held}"


def describe_profile_short(profile) -> str:
    """Return a compact row for the tab's selector, which is narrow."""
    held = "" if profile.is_reachable_now else "  — held"
    return f"{profile.name} — {profile.endpoint_url}{held}"


class ProfileEditorDialog:
    """Modal list of server profiles with add, edit, and delete."""

    def __init__(self, parent, *, db_path=None, on_change: Callable | None = None):
        self._parent = parent
        self._db_path = db_path
        self._on_change = on_change
        self._theme = get_theme()
        self._profiles: tuple = ()

        self._dialog = tk.Toplevel(parent)
        self._dialog.title("Analyst — Model Servers")
        self._dialog.geometry("720x420")
        self._dialog.transient(parent)
        self._theme.apply_to_widget(self._dialog, "main_window")

        self._build()
        self._reload()

        self._dialog.grab_set()
        self._dialog.protocol("WM_DELETE_WINDOW", self._close)
        ensure_dialog_focus(self._dialog, parent)

    # ----------------------------------------------------------------- build

    def _build(self) -> None:
        outer = tk.Frame(self._dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)
        outer.grid_rowconfigure(1, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        heading = tk.Label(
            outer,
            text=(
                "Model servers Analyst can use. Anything off this machine "
                "needs https, or the plaintext acknowledgement."
            ),
        )
        self._theme.apply_to_widget(heading, "label")
        heading.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        wrap_label(heading, outer, padding=32)

        self._listbox = tk.Listbox(outer, height=10, exportselection=False)
        self._theme.apply_to_widget(self._listbox, "listbox")
        self._listbox.grid(row=1, column=0, sticky="nsew")
        self._listbox.bind("<<ListboxSelect>>", self._selection_changed, add="+")

        scroll = ttk.Scrollbar(
            outer, orient=tk.VERTICAL, command=self._listbox.yview,
        )
        scroll.grid(row=1, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=scroll.set)

        self._note_var = tk.StringVar(value="")
        note = tk.Label(outer, textvariable=self._note_var, wraplength=680,
                        justify=tk.LEFT)
        self._theme.apply_to_widget(note, "label")
        note.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 8))

        buttons = tk.Frame(outer)
        self._theme.apply_to_widget(buttons, "main_window")
        buttons.grid(row=3, column=0, columnspan=2, sticky="w")

        self._add_btn = tk.Button(buttons, text="Add…", command=self._add)
        self._theme.apply_to_widget(self._add_btn, "button_secondary")
        self._add_btn.pack(side=tk.LEFT, padx=(0, 7))

        self._edit_btn = tk.Button(buttons, text="Edit…", command=self._edit)
        self._theme.apply_to_widget(self._edit_btn, "button_secondary")
        self._edit_btn.pack(side=tk.LEFT, padx=(0, 7))

        self._delete_btn = tk.Button(buttons, text="Delete", command=self._delete)
        self._theme.apply_to_widget(self._delete_btn, "button_danger")
        self._delete_btn.pack(side=tk.LEFT, padx=(0, 7))

        close = tk.Button(buttons, text="Close", command=self._close)
        self._theme.apply_to_widget(close, "button_primary")
        close.pack(side=tk.LEFT)

    # ------------------------------------------------------------------ data

    def _reload(self, *, select_id: int | None = None) -> None:
        profiles = _profiles()
        try:
            profiles.ensure_default_profile(path=self._db_path)
            self._profiles = profiles.list_profiles(path=self._db_path)
        except Exception as exc:
            self._profiles = ()
            _mb().showerror(
                "Analyst", f"Could not read model servers.\n\n{exc}",
                parent=self._dialog,
            )
        self._listbox.delete(0, tk.END)
        for profile in self._profiles:
            self._listbox.insert(tk.END, describe_profile(profile))
        if self._profiles:
            index = 0
            if select_id is not None:
                for position, profile in enumerate(self._profiles):
                    if profile.profile_id == select_id:
                        index = position
                        break
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(index)
        self._selection_changed()
        if self._on_change is not None:
            self._on_change()

    def _selected(self):
        selection = self._listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self._profiles):
            return self._profiles[index]
        return None

    def _selection_changed(self, _event=None) -> None:
        profile = self._selected()
        if profile is None:
            self._note_var.set("")
            return
        self._note_var.set(
            "" if profile.is_reachable_now else REMOTE_HELD_NOTE
        )

    # --------------------------------------------------------------- actions

    def _add(self) -> None:
        values = _ProfileFormDialog(self._dialog, title="Add Model Server").result
        if values is None:
            return
        profiles = _profiles()
        try:
            created = profiles.create_profile(
                values["name"],
                values["endpoint"],
                backend_kind=values["backend_kind"],
                plaintext_ack=values["plaintext_ack"],
                path=self._db_path,
            )
        except Exception as exc:
            _mb().showerror(
                "Analyst", f"Could not add that server.\n\n{exc}",
                parent=self._dialog,
            )
            return
        self._reload(select_id=created.profile_id)

    def _edit(self) -> None:
        profile = self._selected()
        if profile is None:
            _mb().showinfo(
                "Analyst", "Pick a server to edit.", parent=self._dialog,
            )
            return
        values = _ProfileFormDialog(
            self._dialog, title="Edit Model Server", profile=profile,
        ).result
        if values is None:
            return
        profiles = _profiles()
        try:
            profiles.update_profile(
                profile.profile_id,
                name=values["name"],
                backend_kind=values["backend_kind"],
                plaintext_ack=values["plaintext_ack"],
                endpoint=values["endpoint"],
                path=self._db_path,
            )
        except Exception as exc:
            _mb().showerror(
                "Analyst", f"Could not save that server.\n\n{exc}",
                parent=self._dialog,
            )
            return
        self._reload(select_id=profile.profile_id)

    def _delete(self) -> None:
        profile = self._selected()
        if profile is None:
            _mb().showinfo(
                "Analyst", "Pick a server to delete.", parent=self._dialog,
            )
            return
        confirm = _mb().askyesno(
            "Analyst",
            f"Delete the server profile '{profile.name}'?",
            parent=self._dialog,
        )
        if not confirm:
            return
        profiles = _profiles()
        try:
            profiles.delete_profile(profile.profile_id, path=self._db_path)
        except Exception as exc:
            _mb().showerror(
                "Analyst", f"Could not delete that server.\n\n{exc}",
                parent=self._dialog,
            )
            return
        self._reload()

    def _close(self) -> None:
        try:
            self._dialog.grab_release()
        except Exception:
            pass
        try:
            self._dialog.destroy()
        except Exception:
            pass


class _ProfileFormDialog:
    """Modal add/edit form returning a dict, or None when cancelled."""

    def __init__(self, parent, *, title: str, profile=None):
        self.result = None
        self._theme = get_theme()
        self._dialog = tk.Toplevel(parent)
        self._dialog.title(title)
        self._dialog.transient(parent)
        self._dialog.resizable(False, False)
        self._theme.apply_to_widget(self._dialog, "main_window")

        endpoint = profile.endpoint if profile is not None else None
        self._name_var = tk.StringVar(value="" if profile is None else profile.name)
        self._scheme_var = tk.StringVar(
            value="http" if endpoint is None else endpoint.scheme
        )
        self._host_var = tk.StringVar(
            value="127.0.0.1" if endpoint is None else endpoint.host
        )
        self._port_var = tk.StringVar(
            value="11434" if endpoint is None else str(endpoint.port)
        )
        self._backend_var = tk.StringVar(
            value="ollama" if profile is None else profile.backend_kind.value
        )
        self._ack_var = tk.BooleanVar(
            value=False if profile is None else bool(profile.plaintext_ack)
        )

        self._build()
        self._dialog.grab_set()
        self._dialog.protocol("WM_DELETE_WINDOW", self._cancel)
        ensure_dialog_focus(self._dialog, parent)
        parent.wait_window(self._dialog)

    def _build(self) -> None:
        outer = tk.Frame(self._dialog, padx=14, pady=12)
        self._theme.apply_to_widget(outer, "main_window")
        outer.pack(fill=tk.BOTH, expand=True)
        outer.grid_columnconfigure(1, weight=1)

        rows = (
            ("Name", self._name_var, 28),
            ("Host", self._host_var, 28),
            ("Port", self._port_var, 10),
        )
        for index, (label_text, variable, width) in enumerate(rows):
            label = tk.Label(outer, text=label_text)
            self._theme.apply_to_widget(label, "label")
            label.grid(row=index, column=0, sticky="w", pady=4)
            entry = tk.Entry(outer, textvariable=variable, width=width)
            self._theme.apply_to_widget(entry, "entry")
            entry.grid(row=index, column=1, sticky="w", padx=(8, 0), pady=4)

        scheme_label = tk.Label(outer, text="Scheme")
        self._theme.apply_to_widget(scheme_label, "label")
        scheme_label.grid(row=3, column=0, sticky="w", pady=4)
        scheme = ttk.Combobox(
            outer, textvariable=self._scheme_var, state="readonly",
            values=("http", "https"), width=10,
        )
        scheme.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=4)

        backend_label = tk.Label(outer, text="Backend")
        self._theme.apply_to_widget(backend_label, "label")
        backend_label.grid(row=4, column=0, sticky="w", pady=4)
        backend = ttk.Combobox(
            outer, textvariable=self._backend_var, state="readonly",
            values=("ollama", "openai"), width=12,
        )
        backend.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=4)

        # Contract 4.2: plaintext off this machine is opt-in, per profile, and
        # is only ever accepted for a private address.
        ack = tk.Checkbutton(
            outer,
            text="Allow plaintext to this private address",
            variable=self._ack_var,
        )
        self._theme.apply_to_widget(ack, "checkbox")
        ack.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        note = tk.Label(
            outer,
            text=(
                "A server on this machine needs nothing else. Anywhere else "
                "needs https, or the acknowledgement below."
            ),
            wraplength=380,
            justify=tk.LEFT,
        )
        self._theme.apply_to_widget(note, "label")
        note.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 8))

        buttons = tk.Frame(outer)
        self._theme.apply_to_widget(buttons, "main_window")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e")
        save = tk.Button(buttons, text="Save", command=self._save)
        self._theme.apply_to_widget(save, "button_primary")
        save.pack(side=tk.LEFT, padx=(0, 7))
        cancel = tk.Button(buttons, text="Cancel", command=self._cancel)
        self._theme.apply_to_widget(cancel, "button_secondary")
        cancel.pack(side=tk.LEFT)

    def _save(self) -> None:
        from experimental.analyst.endpoint import Endpoint, EndpointError

        name = self._name_var.get().strip()
        if not name:
            _mb().showerror("Analyst", "Give the server a name.",
                            parent=self._dialog)
            return
        try:
            endpoint = Endpoint(
                scheme=self._scheme_var.get(),
                host=self._host_var.get(),
                port=self._port_var.get().strip(),
            )
        except EndpointError as exc:
            _mb().showerror(
                "Analyst", f"That address is not usable.\n\n{exc}",
                parent=self._dialog,
            )
            return
        self.result = {
            "name": name,
            "endpoint": endpoint.base_url,
            "backend_kind": self._backend_var.get(),
            "plaintext_ack": bool(self._ack_var.get()),
        }
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self._dialog.grab_release()
        except Exception:
            pass
        try:
            self._dialog.destroy()
        except Exception:
            pass


def open_profile_editor(parent, *, db_path=None, on_change=None):
    """Open the modal profile editor and return its controller."""
    return ProfileEditorDialog(parent, db_path=db_path, on_change=on_change)


# --------------------------------------------------------------------------
# Tab-side helpers.  These take the AnalystTab as ``tab``, mirroring the
# dashboard satellites' ``dash`` convention, so analyst_tab keeps only stubs.
# --------------------------------------------------------------------------

def refresh_profile_choices(tab, *, select_id: int | None = None) -> None:
    """Load stored profiles into the tab's selector, defaulting to loopback."""
    profile_store = _profiles()
    try:
        profile_store.ensure_default_profile()
        tab._profile_choices = profile_store.list_profiles()
    except Exception:
        tab._profile_choices = ()
    tab._profile_choices_loaded = True
    combo = tab._profile_combo
    labels = [describe_profile_short(profile) for profile in tab._profile_choices]
    if combo is not None:
        try:
            combo.configure(values=labels)
        except Exception:
            return
    index = 0
    if select_id is not None:
        for position, profile in enumerate(tab._profile_choices):
            if profile.profile_id == select_id:
                index = position
                break
    if labels:
        tab._profile_var.set(labels[index])
        if combo is not None:
            try:
                combo.current(index)
            except Exception:
                pass
    else:
        tab._profile_var.set("")
    profile_selected(tab)


def selected_profile(tab):
    """Return the tab's chosen profile, or None when none is stored.

    The selector widget belongs to the advanced dialog and dies with it. A
    launch happens after that dialog has closed, so the widget cannot be the
    only record of the choice: reading a destroyed combobox raised, fell back
    to the first profile -- the loopback Ollama -- and ran a llama.cpp model
    against it. The saved profile id is the record that outlives the widget.
    """
    combo = tab._profile_combo
    if combo is not None:
        try:
            index = combo.current()
        except Exception:
            index = -1
        if 0 <= index < len(tab._profile_choices):
            return tab._profile_choices[index]
    choices = tab._profile_choices
    if not choices and not getattr(tab, "_profile_choices_loaded", False):
        # The dialog has never been opened, so nothing has loaded the list.
        try:
            choices = _profiles().list_profiles()
        except Exception:
            choices = ()
        tab._profile_choices = choices
        tab._profile_choices_loaded = True
    wanted = saved_profile_id(tab)
    if wanted is not None:
        for profile in choices:
            if profile.profile_id == wanted:
                return profile
    return choices[0] if choices else None


def selected_endpoint(tab) -> str:
    """Return the tab's chosen endpoint, or the loopback default."""
    from experimental.analyst.endpoint import DEFAULT_ENDPOINT

    profile = selected_profile(tab)
    return DEFAULT_ENDPOINT if profile is None else profile.endpoint_url


def profile_selected(tab, _event=None) -> None:
    """Show what is missing when the chosen profile cannot be contacted."""
    profile = selected_profile(tab)
    if profile is None or profile.is_reachable_now:
        tab._profile_note_var.set("")
    else:
        tab._profile_note_var.set(REMOTE_HELD_NOTE)


def clear_model_if_server_changed(tab) -> None:
    """Drop a selected model that came from a different server.

    A model name only means something on the server that listed it. Keeping one
    across a profile change let a llama.cpp model be launched as though it were
    a digest-pinned Ollama model, which fails deep in run creation with a
    misleading message.
    """
    profile = selected_profile(tab)
    current = None if profile is None else profile.profile_id
    previous = getattr(tab, "_model_profile_id", None)
    tab._model_profile_id = current
    if previous is None or previous == current:
        return
    tab._model_choices = []
    tab._selected_model_tag = None
    tab._selected_model_digest = None
    tab._model_var.set("")
    combo = getattr(tab, "_model_combo", None)
    if combo is not None:
        try:
            combo.configure(values=())
        except Exception:
            pass
    status = getattr(tab, "_model_status_var", None)
    if status is not None:
        status.set("Server changed. Connect / Refresh to list its models.")


def manage_profiles(tab) -> None:
    """Open the editor over the advanced dialog and reload the selector."""
    dialog = tab._advanced_dialog
    if dialog is None:
        return
    open_profile_editor(dialog)
    refresh_profile_choices(tab)


class ReportedModel:
    """One model a server reported, with no digest to pin it (erratum E19)."""

    __slots__ = ("model_tag", "model_digest")

    def __init__(self, model_tag: str) -> None:
        self.model_tag = model_tag
        self.model_digest = None


def discover_models(tab) -> None:
    """Load the chosen server's model list, off the Tk thread.

    Dispatches on the profile's backend kind. An Ollama catalogue is charged
    and persisted as before; an OpenAI-compatible one is read live, because a
    reported model has no digest and the persisted table requires one.
    """
    import threading

    dialog = tab._advanced_dialog
    button = tab._model_connect_btn
    if dialog is None or button is None:
        return
    button.configure(state="disabled")
    if tab._model_status_var is not None:
        tab._model_status_var.set("Connecting to the model server\u2026")
    preferred_tag = tab._model_var.get()
    endpoint = tab._selected_endpoint()
    profile = selected_profile(tab)
    kind = tab._selected_backend_kind()
    ack = bool(profile.plaintext_ack) if profile is not None else False

    def work() -> None:
        try:
            if kind == "openai":
                from experimental.analyst.service import discover_reported_models

                choices = tuple(
                    ReportedModel(name)
                    for name in discover_reported_models(
                        endpoint, plaintext_ack=ack,
                    )
                )
            else:
                from experimental.analyst.service import discover_models as _discover

                choices = _discover(endpoint=endpoint)
        except Exception as exc:
            held = type(exc).__name__ == "AddressPolicyError"
            tab._schedule(
                lambda: tab._finish_model_discovery(
                    dialog, None, preferred_tag, held=held,
                )
            )
            return
        tab._schedule(
            lambda: tab._finish_model_discovery(dialog, choices, preferred_tag)
        )

    threading.Thread(target=work, daemon=True).start()


def persist_model_selection(tab) -> None:
    """Save the chosen model and the server it came from."""
    selected_tag = None
    selected_digest = None
    combo = tab._model_combo
    if combo is not None:
        index = combo.current()
        if 0 <= index < len(tab._model_choices):
            choice = tab._model_choices[index]
            selected_tag = choice.model_tag
            selected_digest = choice.model_digest
        elif (
            not tab._model_choices
            and tab._selected_model_tag is not None
            and tab._model_var.get() == tab._selected_model_tag
        ):
            selected_tag = tab._selected_model_tag
            selected_digest = tab._selected_model_digest
    tab._selected_model_tag = selected_tag
    tab._selected_model_digest = selected_digest

    settings_manager = tab._context.get("settings_manager")
    if settings_manager is None:
        return
    try:
        settings_manager.set_setting(
            "analyst.selected_model_tag", selected_tag,
        )
        settings_manager.set_setting(
            "analyst.selected_model_digest", selected_digest,
        )
        # The server the model came from. Without this the selector reverts
        # to the first profile on reopen, and a reported model then looks
        # like a digest model with a missing digest.
        profile = selected_profile(tab)
        settings_manager.set_setting(
            "analyst.selected_profile_id",
            None if profile is None else profile.profile_id,
        )
    except Exception:
        pass

def load_selected_model(settings_manager) -> tuple[str | None, str | None]:
    if settings_manager is None:
        return None, None
    try:
        tag = settings_manager.get_setting(
            "analyst.selected_model_tag", None,
        )
        digest = settings_manager.get_setting(
            "analyst.selected_model_digest", None,
        )
    except Exception:
        return None, None
    if type(tag) is not str or not tag:
        return None, None
    # A reported model has no digest. Requiring one here silently dropped
    # every llama.cpp selection on reopen.
    if type(digest) is not str or not digest:
        return tag, None
    return tag, digest

def saved_profile_id(tab) -> int | None:
    """Return the profile this tab last ran against, if it still exists."""
    context = getattr(tab, "_context", None)
    settings_manager = None if context is None else context.get("settings_manager")
    if settings_manager is None:
        return None
    try:
        value = settings_manager.get_setting("analyst.selected_profile_id", None)
    except Exception:
        return None
    return value if type(value) is int and value > 0 else None


__all__ = [
    "REMOTE_HELD_NOTE",
    "ProfileEditorDialog",
    "ReportedModel",
    "describe_profile",
    "clear_model_if_server_changed",
    "describe_profile_short",
    "load_selected_model",
    "persist_model_selection",
    "saved_profile_id",
    "discover_models",
    "manage_profiles",
    "open_profile_editor",
    "profile_selected",
    "refresh_profile_choices",
    "selected_endpoint",
    "selected_profile",
]
