from typing import Any, Dict, List


_FATAL_STATUSES = {"DEPENDENCY_MISSING", "NORMALIZATION_ERROR"}


def _get_smb_adapter(op):
    adapter = getattr(op, "_smb_adapter", None)
    if adapter is not None:
        return adapter

    from shared.smb_adapter import SMBAdapter

    timeout = op.config.get_connection_timeout()
    adapter = SMBAdapter(timeout_seconds=timeout)
    setattr(op, "_smb_adapter", adapter)
    return adapter


def preflight_access_backend(op) -> None:
    """Fail fast when required SMB Python backends are unavailable."""
    adapter = _get_smb_adapter(op)
    adapter.ensure_backend_available("impacket")


def enumerate_shares_detailed(op, ip, username, password) -> Dict[str, Any]:
    """
    Enumerate shares and return detailed status for fatal/non-fatal handling.
    """
    try:
        adapter = _get_smb_adapter(op)
        result = adapter.list_shares(
            ip,
            username=username,
            password=password,
            cautious_mode=op.cautious_mode,
            timeout_seconds=op.config.get_connection_timeout(),
        )
    except Exception as e:
        message = str(e)
        return {
            "success": False,
            "fatal": True,
            "shares": [],
            "status_code": "ERROR",
            "error": message,
        }

    status_code = str(result.get("status_code") or "ERROR")
    if not result.get("success"):
        error = result.get("error", "unknown error")
        fatal = status_code in _FATAL_STATUSES
        return {
            "success": False,
            "fatal": fatal,
            "shares": [],
            "status_code": status_code,
            "error": error,
        }

    shares: List[str] = []
    for share in result.get("shares", []):
        share_name = str(share.get("name", "")).strip()
        is_disk = bool(share.get("is_disk"))
        is_admin = bool(share.get("is_admin"))

        if not share_name.replace("_", "").replace("-", "").isalnum():
            op.output.print_if_verbose(f"Skipping invalid share name format: {share_name}")
            continue

        if is_admin:
            op.output.print_if_verbose(f"Skipped administrative share: {share_name}")
            continue

        if not is_disk:
            op.output.print_if_verbose(f"Skipped non-disk share: {share_name}")
            continue

        shares.append(share_name)
        op.output.print_if_verbose(f"Added share: {share_name}")

    op.output.print_if_verbose(f"Found {len(shares)} non-admin shares")
    return {
        "success": True,
        "fatal": False,
        "shares": shares,
        "status_code": "OK",
        "error": None,
    }


def enumerate_shares(op, ip, username, password) -> List[str]:
    """Backward-compatible list-returning wrapper for share enumeration."""
    detailed = enumerate_shares_detailed(op, ip, username, password)
    return detailed.get("shares", [])
