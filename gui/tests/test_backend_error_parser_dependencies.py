from gui.utils.backend_interface.error_parser import extract_error_details


def test_extract_error_details_flags_missing_impacket_dependency():
    output = "Traceback... ModuleNotFoundError: No module named 'impacket'"

    detail = extract_error_details(output, ["python3", "cli/smbseek.py"])

    assert detail.startswith("DEPENDENCY_MISSING:")
    assert "smbprotocol/impacket/pyspnego" in detail
    assert "impacket" in detail
