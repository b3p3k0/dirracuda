"""Opt-in, UI-thread-safe Analyst handoff after extraction persistence."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from gui.utils import safe_messagebox
from shared.extract_manifest import ExtractSummaryReference, ExtractSummarySource


SETTING_KEY = "analyst.offer_after_extract"


def offer_after_extract(
    parent: Any,
    settings_manager: Any,
    reference: object,
    *,
    main_db_path: Path | None,
    report_label: str,
) -> bool:
    """Offer one exact persisted manifest; return whether a prompt was shown."""
    if type(reference) is not ExtractSummaryReference:
        return False
    try:
        enabled = settings_manager is not None and (
            settings_manager.get_setting(SETTING_KEY, False) is True
        )
    except Exception:
        enabled = False
    if not enabled:
        return False
    if (
        reference.source is ExtractSummarySource.PRIMARY_DB
        and (not isinstance(main_db_path, Path) or not main_db_path.is_absolute())
    ):
        return False
    if type(report_label) is not str or not report_label.strip():
        return False
    accepted = safe_messagebox.askyesno(
        "Analyze Extracted Files?",
        "The extraction manifest is safely persisted.\n\n"
        f"Analyze only its saved files for {report_label.strip()} now?",
        parent=parent,
    )
    if not accepted:
        return True

    completed: queue.SimpleQueue[bool] = queue.SimpleQueue()

    def work() -> None:
        try:
            from experimental.analyst.service import create_manifest_and_launch

            create_manifest_and_launch(
                reference,
                main_db_path=main_db_path,
                output_base=None,
                report_label=report_label.strip(),
                mode="fast",
            )
        except Exception:
            completed.put(False)
        else:
            completed.put(True)

    def poll() -> None:
        try:
            success = completed.get_nowait()
        except queue.Empty:
            try:
                parent.after(50, poll)
            except Exception:
                pass
            return
        if success:
            safe_messagebox.showinfo(
                "Analyst", "The durable Analyst run was created and launched.",
                parent=parent,
            )
        else:
            safe_messagebox.showerror(
                "Analyst", "The Analyst run could not be created or launched.",
                parent=parent,
            )

    threading.Thread(target=work, daemon=True).start()
    parent.after(50, poll)
    return True


__all__ = ["SETTING_KEY", "offer_after_extract"]
