"""Scan results dialog wording tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gui.components.scan_results_dialog import ScanResultsDialog


def _dialog(protocol: str, protocols: list[str] | None = None) -> ScanResultsDialog:
    dialog = ScanResultsDialog.__new__(ScanResultsDialog)
    dialog.protocol = protocol
    dialog.scan_results = {"protocol": protocol}
    if protocols is not None:
        dialog.scan_results["protocols"] = protocols
    return dialog


def test_multi_protocol_wording_is_generic() -> None:
    dialog = _dialog("multi", ["smb", "ftp", "http"])
    assert dialog._success_subtitle() == "Multi-protocol scan queue has finished successfully."
    assert dialog._shares_label() == "Resources Found:"
    assert dialog._access_phrase() == "resources"


def test_single_protocol_wording_remains_specific() -> None:
    ftp = _dialog("ftp")
    assert ftp._success_subtitle() == "FTP scan has finished successfully."
    assert ftp._shares_label() == "Directories Found:"
    assert ftp._access_phrase() == "accessible FTP directories"

    smb = _dialog("smb")
    assert smb._success_subtitle() == "SMB security scan has finished successfully."
    assert smb._shares_label() == "Shares Found:"
    assert smb._access_phrase() == "accessible SMB shares"
