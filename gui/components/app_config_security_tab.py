"""Security tab card builders extracted from AppConfigDialog (C6B)."""

import sys
import tkinter as tk

from gui.utils import safe_messagebox as messagebox  # noqa: F401 — keep for satellite discipline


def create_tmpfs_card(dialog, security_tab) -> None:
    card = tk.Frame(security_tab, highlightthickness=1, bd=0)
    dialog.theme.apply_to_widget(card, "card")
    try:
        card.configure(
            highlightbackground=dialog.theme.colors["border"],
            highlightcolor=dialog.theme.colors["border"],
        )
    except tk.TclError:
        pass
    card.pack(fill=tk.X, pady=(0, 10))

    heading = dialog.theme.create_styled_label(card, "In-Memory Quarantine (tmpfs)", "body")
    heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

    row1 = tk.Frame(card)
    dialog.theme.apply_to_widget(row1, "card")
    row1.pack(fill=tk.X, padx=10, pady=(0, 6))
    dialog.quarantine_tmpfs_enabled_var = tk.BooleanVar(value=dialog.quarantine_tmpfs_enabled)
    cb_tmpfs = tk.Checkbutton(
        row1,
        text="Use memory (tmpfs) for quarantine",
        variable=dialog.quarantine_tmpfs_enabled_var,
        command=dialog._sync_quarantine_controls_for_tmpfs,
    )
    dialog.theme.apply_to_widget(cb_tmpfs, "checkbox")
    cb_tmpfs.pack(anchor=tk.W)

    dialog.quarantine_tmpfs_note_label = dialog.theme.create_styled_label(
        card,
        "",
        "small",
        fg=dialog.theme.colors["text_secondary"],
    )
    dialog.quarantine_tmpfs_note_label.pack(anchor=tk.W, padx=12, pady=(0, 10))

    if not dialog._tmpfs_supported_platform:
        if dialog.quarantine_tmpfs_enabled_var:
            dialog.quarantine_tmpfs_enabled_var.set(False)
        try:
            cb_tmpfs.configure(state=tk.DISABLED)
        except tk.TclError:
            pass


def create_http_tls_card(dialog, security_tab) -> None:
    card = tk.Frame(security_tab, highlightthickness=1, bd=0)
    dialog.theme.apply_to_widget(card, "card")
    try:
        card.configure(
            highlightbackground=dialog.theme.colors["border"],
            highlightcolor=dialog.theme.colors["border"],
        )
    except tk.TclError:
        pass
    card.pack(fill=tk.X, pady=(0, 10))

    heading = dialog.theme.create_styled_label(card, "HTTP Target TLS", "body")
    heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

    row1 = tk.Frame(card)
    dialog.theme.apply_to_widget(row1, "card")
    row1.pack(fill=tk.X, padx=10, pady=(0, 6))
    dialog.http_tls_allow_insecure_var = tk.BooleanVar(value=dialog.http_tls_allow_insecure)
    cb_tls = tk.Checkbutton(
        row1,
        text="Allow insecure TLS for HTTP targets (skip certificate verification)",
        variable=dialog.http_tls_allow_insecure_var,
    )
    dialog.theme.apply_to_widget(cb_tls, "checkbox")
    cb_tls.pack(anchor=tk.W)

    note = dialog.theme.create_styled_label(
        card,
        "When enabled, certificate and hostname checks are skipped for HTTPS targets — "
        "connections can be intercepted or impersonated (machine-in-the-middle). This is "
        "the application default; a scan dialog choice applies to that run only.",
        "small",
        fg=dialog.theme.colors["text_secondary"],
    )
    note.pack(anchor=tk.W, padx=12, pady=(0, 10))


def create_clamav_card(dialog, security_tab) -> None:
    card = tk.Frame(security_tab, highlightthickness=1, bd=0)
    dialog.theme.apply_to_widget(card, "card")
    try:
        card.configure(
            highlightbackground=dialog.theme.colors["border"],
            highlightcolor=dialog.theme.colors["border"],
        )
    except tk.TclError:
        pass
    card.pack(fill=tk.X, pady=(0, 10))

    heading = dialog.theme.create_styled_label(card, "ClamAV Settings", "body")
    heading.pack(anchor=tk.W, padx=12, pady=(10, 6))

    row1 = tk.Frame(card)
    dialog.theme.apply_to_widget(row1, "card")
    row1.pack(fill=tk.X, padx=10, pady=(0, 6))
    dialog.clamav_enabled_var = tk.BooleanVar(value=dialog.clamav_enabled)
    cb_enable = tk.Checkbutton(row1, text="Enable ClamAV scanning", variable=dialog.clamav_enabled_var)
    dialog.theme.apply_to_widget(cb_enable, "checkbox")
    cb_enable.pack(side=tk.LEFT)

    dialog.clamav_auto_promote_clean_var = tk.BooleanVar(value=dialog.clamav_auto_promote_clean)
    cb_promote_clean = tk.Checkbutton(
        row1,
        text="Automatically promote clean files",
        variable=dialog.clamav_auto_promote_clean_var,
    )
    dialog.theme.apply_to_widget(cb_promote_clean, "checkbox")
    cb_promote_clean.pack(side=tk.LEFT, padx=(14, 0))

    row2 = tk.Frame(card)
    dialog.theme.apply_to_widget(row2, "card")
    row2.pack(fill=tk.X, padx=10, pady=(0, 6))
    lbl_backend = dialog.theme.create_styled_label(
        row2, "Backend:", "small", fg=dialog.theme.colors["text_secondary"]
    )
    lbl_backend.pack(side=tk.LEFT, padx=(0, 8))
    dialog.clamav_backend_var = tk.StringVar(value=dialog.clamav_backend)
    opt_backend = tk.OptionMenu(row2, dialog.clamav_backend_var, "auto", "clamdscan", "clamscan")
    dialog.theme.apply_to_widget(opt_backend, "button_secondary")
    opt_backend.pack(side=tk.LEFT)

    row3 = tk.Frame(card)
    dialog.theme.apply_to_widget(row3, "card")
    row3.pack(fill=tk.X, padx=10, pady=(0, 6))
    lbl_timeout = dialog.theme.create_styled_label(
        row3, "Timeout:", "small", fg=dialog.theme.colors["text_secondary"]
    )
    lbl_timeout.pack(side=tk.LEFT, padx=(0, 8))
    dialog.clamav_timeout_var = tk.StringVar(value=str(dialog.clamav_timeout))
    entry_timeout = tk.Entry(row3, textvariable=dialog.clamav_timeout_var, font=("Arial", 10), width=6)
    dialog.theme.apply_to_widget(entry_timeout, "entry")
    entry_timeout.pack(side=tk.LEFT)
    lbl_sec = dialog.theme.create_styled_label(
        row3, "seconds", "small", fg=dialog.theme.colors["text_secondary"]
    )
    lbl_sec.pack(side=tk.LEFT, padx=(6, 0))

    row4 = tk.Frame(card)
    dialog.theme.apply_to_widget(row4, "card")
    row4.pack(fill=tk.X, padx=10, pady=(0, 6))
    lbl_root = dialog.theme.create_styled_label(
        row4, "Extracted root:", "small", fg=dialog.theme.colors["text_secondary"]
    )
    lbl_root.pack(side=tk.LEFT, padx=(0, 8))
    dialog.clamav_extracted_root_var = tk.StringVar(value=dialog.clamav_extracted_root)
    entry_root = tk.Entry(
        row4, textvariable=dialog.clamav_extracted_root_var, font=("Arial", 10)
    )
    dialog.theme.apply_to_widget(entry_root, "entry")
    entry_root.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    browse_root = tk.Button(
        row4,
        text="Browse...",
        command=lambda: dialog._browse_path("clamav_extracted_root"),
    )
    dialog.theme.apply_to_widget(browse_root, "button_secondary")
    browse_root.pack(side=tk.LEFT)

    row5 = tk.Frame(card)
    dialog.theme.apply_to_widget(row5, "card")
    row5.pack(fill=tk.X, padx=10, pady=(0, 6))
    lbl_kb = dialog.theme.create_styled_label(
        row5, "Known-bad subfolder:", "small", fg=dialog.theme.colors["text_secondary"]
    )
    lbl_kb.pack(side=tk.LEFT, padx=(0, 8))
    dialog.clamav_known_bad_subdir_var = tk.StringVar(value=dialog.clamav_known_bad_subdir)
    entry_kb = tk.Entry(
        row5, textvariable=dialog.clamav_known_bad_subdir_var, font=("Arial", 10)
    )
    dialog.theme.apply_to_widget(entry_kb, "entry")
    entry_kb.pack(side=tk.LEFT, fill=tk.X, expand=True)

    row6 = tk.Frame(card)
    dialog.theme.apply_to_widget(row6, "card")
    row6.pack(fill=tk.X, padx=10, pady=(0, 10))
    dialog.clamav_show_results_var = tk.BooleanVar(value=dialog.clamav_show_results)
    cb_show = tk.Checkbutton(
        row6, text="Show results dialog after extract", variable=dialog.clamav_show_results_var
    )
    dialog.theme.apply_to_widget(cb_show, "checkbox")
    cb_show.pack(anchor=tk.W)
