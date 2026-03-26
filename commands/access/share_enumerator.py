from typing import List


def _get_smb_adapter(op):
    adapter = getattr(op, "_smb_adapter", None)
    if adapter is not None:
        return adapter

    from shared.smb_adapter import SMBAdapter

    timeout = op.config.get_connection_timeout()
    adapter = SMBAdapter(timeout_seconds=timeout)
    setattr(op, "_smb_adapter", adapter)
    return adapter


def enumerate_shares(op, ip, username, password) -> List[str]:
    """Enumerate available SMB shares on the target server via adapter API."""
    try:
        adapter = _get_smb_adapter(op)
        result = adapter.list_shares(
            ip,
            username=username,
            password=password,
            cautious_mode=op.cautious_mode,
            timeout_seconds=op.config.get_connection_timeout(),
        )

        if not result.get("success"):
            op.output.print_if_verbose(
                f"Share enumeration failed on {ip}: {result.get('error', 'unknown error')}"
            )
            return []

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
        return shares

    except Exception as e:
        op.output.print_if_verbose(f"Share enumeration failed: {str(e)}")

    return []
