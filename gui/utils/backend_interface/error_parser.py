"""
Error parsing helpers for BackendInterface.
"""

import re
from typing import List


def extract_error_details(full_output: str, cmd: List[str]) -> str:
    """
    Extract meaningful error details from SMBSeek CLI output with enhanced
    error handling for recent filtering scenarios.
    """
    lines = full_output.split('\n')

    # Check for specific recent filtering errors first
    for line in lines:
        line_clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()

        if "No authenticated hosts found from the last" in line_clean:
            return f"RECENT_HOSTS_ERROR: {line_clean}"

        if "None of the specified servers are authenticated" in line_clean:
            return f"SERVERS_NOT_AUTHENTICATED: {line_clean}"

        missing_dependency_substrings = (
            "SMB libraries not available",
            "ModuleNotFoundError: No module named 'smbprotocol'",
            'ModuleNotFoundError: No module named "smbprotocol"',
            "ModuleNotFoundError: No module named 'pyspnego'",
            'ModuleNotFoundError: No module named "pyspnego"',
            "ModuleNotFoundError: No module named 'impacket'",
            'ModuleNotFoundError: No module named "impacket"',
            "No module named 'smbprotocol'",
            'No module named "smbprotocol"',
            "No module named 'pyspnego'",
            'No module named "pyspnego"',
            "No module named 'impacket'",
            'No module named "impacket"'
        )
        if any(substring in line_clean for substring in missing_dependency_substrings):
            friendly_message = (
                "Dirracuda backend is missing required SMB libraries "
                "(smbprotocol/impacket/pyspnego). "
                "This usually happens when Dirracuda runs outside the project "
                "virtual environment. Activate the venv (e.g., `source venv/bin/activate`) "
                "or install the dependencies with `pip install -r requirements.txt`.\n"
                f"Backend output: {line_clean}"
            )
            return f"DEPENDENCY_MISSING: {friendly_message}"

    error_indicators = [
        'error:', 'Error:', 'ERROR:',
        'failed:', 'Failed:', 'FAILED:',
        'exception:', 'Exception:', 'EXCEPTION:',
        'traceback', 'Traceback',
        'invalid', 'Invalid', 'INVALID',
        'missing', 'Missing', 'MISSING',
        'not found', 'Not found', 'NOT FOUND'
    ]

    error_lines = []
    for line in lines:
        line_lower = line.lower().strip()
        if any(indicator.lower() in line_lower for indicator in error_indicators):
            clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
            if clean_line:
                error_lines.append(clean_line)

    if error_lines:
        return '\n'.join(error_lines[:3])

    non_empty_lines = [line.strip() for line in lines if line.strip()]
    if non_empty_lines:
        last_lines = non_empty_lines[-3:]
        clean_lines = [re.sub(r'\x1b\[[0-9;]*m', '', line) for line in last_lines]
        return '\n'.join(clean_lines)

    return f"Command failed: {' '.join(cmd[:3])}{'...' if len(cmd) > 3 else ''}"
