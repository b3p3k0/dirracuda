import re
from typing import Optional

from shared.smb_adapter import SMBAdapter, SMB_STATUS_HINTS


def test_share_access(op, ip, share_name, username, password):
    """Test read access to a specific SMB share via the pure-Python SMB adapter."""
    access_result = {
        'share_name': share_name,
        'accessible': False,
        'error': None,
        'auth_status': None
    }

    try:
        adapter = _get_smb_adapter(op)
        op.output.print_if_verbose(
            f"Testing access via SMB adapter: //{ip}/{share_name}"
        )

        result = adapter.probe_share_read(
            ip,
            share_name=share_name,
            username=username,
            password=password,
            cautious_mode=op.cautious_mode,
            timeout_seconds=op.config.get_connection_timeout(),
        )

        status_code = _normalize_status_code(result.get("status_code"), result.get("error"))
        accessible = bool(result.get("accessible"))
        error_message = _build_error_message(status_code, result.get("error"))

        if accessible:
            access_result['accessible'] = True
            access_result['auth_status'] = "OK"
            op.output.print_if_verbose(f"Share '{share_name}' is accessible")
        else:
            access_result['error'] = error_message
            access_result['auth_status'] = status_code

            share_missing = status_code == "NT_STATUS_BAD_NETWORK_NAME"
            timeout_error = status_code == "TIMEOUT"
            is_expected_denial = status_code in {"ACCESS_DENIED", "NT_STATUS_ACCESS_DENIED"}
            if share_missing:
                op.output.print_if_verbose(f"Share '{share_name}' was not found on target")
            elif timeout_error:
                op.output.warning(
                    f"Share '{share_name}' - timeout (consider increasing share access timeout if this is frequent)"
                )
            elif is_expected_denial:
                op.output.print_if_verbose(f"Share '{share_name}' - no readable content")
            else:
                op.output.error(f"Share '{share_name}' - {error_message}")

            if op.cautious_mode and status_code.startswith("NT_STATUS_"):
                if "ACCESS_DENIED" in status_code or "LOGON_FAILURE" in status_code:
                    op.output.print_if_verbose(
                        f"Share '{share_name}' access denied - security restrictions in cautious mode"
                    )

    except TimeoutError:
        access_result['error'] = "Connection timed out"
        access_result['auth_status'] = "TIMEOUT"
        op.output.warning(
            f"Share '{share_name}' - timeout (consider increasing share access timeout if this is frequent)"
        )
    except Exception as e:
        access_result['error'] = f"Test error: {str(e)}"
        access_result['auth_status'] = "ERROR"
        op.output.warning(f"Share '{share_name}' - test error: {str(e)}")

    return access_result


def _get_smb_adapter(op):
    adapter = getattr(op, "_smb_adapter", None)
    if adapter is not None:
        return adapter

    timeout = op.config.get_connection_timeout()
    adapter = SMBAdapter(timeout_seconds=timeout)
    setattr(op, "_smb_adapter", adapter)
    return adapter


def _normalize_status_code(status_code: Optional[str], error_message: Optional[str]) -> str:
    if status_code:
        normalized = str(status_code).strip().upper()
        if normalized.startswith("STATUS_"):
            normalized = f"NT_{normalized}"
        if normalized == "BAD_NETWORK_NAME":
            return "NT_STATUS_BAD_NETWORK_NAME"
        if normalized and normalized != "ERROR":
            return normalized

    nt_status = _extract_nt_status(error_message or "")
    if nt_status:
        return nt_status

    message_lower = (error_message or "").lower()
    if "timeout" in message_lower or "timed out" in message_lower:
        return "TIMEOUT"
    if "share not found" in message_lower or "bad network name" in message_lower:
        return "NT_STATUS_BAD_NETWORK_NAME"
    if "access denied" in message_lower:
        return "NT_STATUS_ACCESS_DENIED"
    if "logon failure" in message_lower or "authentication failed" in message_lower:
        return "NT_STATUS_LOGON_FAILURE"

    return "ERROR"


def _build_error_message(status_code: str, error_message: Optional[str]) -> Optional[str]:
    if status_code == "NT_STATUS_BAD_NETWORK_NAME":
        return "Share not found on server (server reported NT_STATUS_BAD_NETWORK_NAME)"
    if status_code in {"ACCESS_DENIED", "NT_STATUS_ACCESS_DENIED"} and not error_message:
        return "Access denied or empty share"
    if status_code == "TIMEOUT":
        return error_message or "Connection timed out"
    return error_message or f"SMB protocol error ({status_code})"


def _format_smbclient_error(result):
    """
    Legacy formatter kept for compatibility with operation wrappers.
    """
    def _clean(stream: Optional[str]) -> str:
        if not stream:
            return ""
        return stream.strip().rstrip("~").strip()

    stderr_trimmed = _clean(result.stderr)
    stdout_trimmed = _clean(result.stdout)

    if stderr_trimmed and stdout_trimmed:
        combined_output = f"{stderr_trimmed} | {stdout_trimmed}"
    elif stderr_trimmed:
        combined_output = stderr_trimmed
    elif stdout_trimmed:
        combined_output = stdout_trimmed
    else:
        combined_output = ""

    if not combined_output:
        return (f"smbclient exited with code {result.returncode} and produced no output", "")

    nt_status_match = re.search(r'(NT_STATUS_[A-Z_]+)', combined_output)

    if nt_status_match and nt_status_match.group(1) == 'NT_STATUS_ACCESS_DENIED':
        combined_lower = combined_output.lower()
        if 'tree connect failed' in combined_lower and 'anonymous login successful' in combined_lower:
            friendly_msg = 'Access denied - share does not allow anonymous/guest browsing (NT_STATUS_ACCESS_DENIED)'
            return (friendly_msg, None)

    if nt_status_match and nt_status_match.group(1) == 'NT_STATUS_LOGON_FAILURE':
        combined_lower = combined_output.lower()
        if 'tree connect failed' in combined_lower:
            friendly_msg = 'Authentication failed for this share'
            return (friendly_msg, None)

    if nt_status_match:
        status_code = nt_status_match.group(1)
        hint = SMB_STATUS_HINTS.get(status_code, "SMB protocol error")

        if status_code in ('NT_STATUS_IO_TIMEOUT', 'NT_STATUS_CONNECTION_REFUSED',
                           'NT_STATUS_HOST_UNREACHABLE', 'NT_STATUS_NETWORK_UNREACHABLE'):
            ip_match = re.search(r"Connection to\s+([^\s)]+)", combined_output)
            target = ip_match.group(1) if ip_match else "target host"
            friendly_msg = f"{hint} while reaching {target}"
            return (friendly_msg, None)

        if status_code == 'NT_STATUS_BAD_NETWORK_NAME':
            friendly_msg = "Share not found on server"
            return (friendly_msg, None)

        start_pos = max(0, nt_status_match.start() - 80)
        end_pos = min(len(combined_output), nt_status_match.end() + 80)
        context = combined_output[start_pos:end_pos]

        if len(context) > 160:
            context = context[:157] + "..."

        friendly_msg = f"{hint} ({status_code}) - {context}"
        raw_ctx = combined_output if combined_output != friendly_msg else None
        return (friendly_msg, raw_ctx)
    else:
        trimmed_output = combined_output[:160] + "..." if len(combined_output) > 160 else combined_output
        return (f"smbclient error: {trimmed_output}", combined_output)


def _extract_nt_status(message: str) -> Optional[str]:
    """Return first NT_STATUS_* token in the provided message, if present."""
    if not message:
        return None
    marker = "NT_STATUS_"
    upper = message.upper()
    if marker not in upper:
        return None
    match = re.search(r"(NT_STATUS_[A-Z0-9_]+)", upper)
    if match:
        return match.group(1)
    return None
