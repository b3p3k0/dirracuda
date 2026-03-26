import subprocess
from typing import List


def check_smbclient_availability() -> bool:
    """Check if smbclient command is available on the system."""
    try:
        result = subprocess.run(['smbclient', '--help'], capture_output=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False


def build_smbclient_cmd(op, operation_type, target, username="", password="", **kwargs):
    """
    Build complete smbclient command with security options and credentials.
    """
    cmd = ["smbclient"]

    if op.cautious_mode:
        cmd.extend([
            "--client-protection=sign",
            "--max-protocol=SMB3",
            "--option=client min protocol=SMB2",
            "--option=client smb encrypt=desired"
        ])

    if operation_type == "enumerate":
        cmd.extend(["-L", f"//{target}"])
    elif operation_type == "access":
        share = kwargs.get('share')
        cmd.extend([f"//{target}/{share}"])

    if username == "" and password == "":
        cmd.append("-N")
    elif username == "guest":
        if password == "":
            cmd.extend(["--user", "guest%"])
        else:
            cmd.extend(["--user", f"guest%{password}"])
    else:
        cmd.extend(["--user", f"{username}%{password}"])

    return cmd


def execute_with_fallback(op, cmd, **kwargs):
    """
    Execute smbclient command with fallback for unsupported security flags.
    """
    try:
        result = subprocess.run(cmd, **kwargs)
        return result
    except subprocess.CalledProcessError as e:
        if op.cautious_mode and "Unknown option" in (e.stderr or ""):
            fallback_cmd = [arg for arg in cmd if not arg.startswith("--client-protection")]

            if "--client-protection=sign" in cmd:
                insert_pos = 1
                fallback_cmd.insert(insert_pos, "--option=client signing=required")

            op.output.print_if_verbose("Falling back to older smbclient syntax for security options")
            return subprocess.run(fallback_cmd, **kwargs)
        raise


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

            if not share_name.replace('_', '').replace('-', '').isalnum():
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


def _is_section_header(op, line: str) -> bool:
    """Check if line is an actual smbclient section header, not a share name."""
    line_lower = line.strip().lower()

    if line_lower.startswith("server") and "comment" in line_lower:
        return True
    if line_lower.startswith("workgroup") and "master" in line_lower:
        return True
    if line_lower.startswith("domain") and "controller" in line_lower:
        return True
    if line_lower.startswith("session request"):
        return True

    return False


def parse_share_list(op, smbclient_output: str) -> List[str]:
    """Parse smbclient -L output to extract non-administrative share names."""
    shares: List[str] = []
    lines = smbclient_output.split('\n')
    in_share_section = False
    share_section_ended = False

    op.output.print_if_verbose("Parsing smbclient share list output")

    for line_num, line in enumerate(lines):
        line = line.strip()

        if share_section_ended:
            break

        if not in_share_section and "Sharename" in line and "Type" in line:
            in_share_section = True
            op.output.print_if_verbose(f"Found shares section header at line {line_num + 1}")
            continue

        if in_share_section and line.startswith("-"):
            continue

        if in_share_section:
            if line == "":
                for next_line in lines[line_num + 1:line_num + 3]:
                    next_line = next_line.strip()
                    if next_line:
                        if _is_section_header(op, next_line):
                            share_section_ended = True
                            op.output.print_if_verbose(f"Detected end of shares section at line {line_num + 1}")
                        break
                continue
            elif _is_section_header(op, line):
                share_section_ended = True
                op.output.print_if_verbose(f"Found section end marker at line {line_num + 1}: {line[:30]}...")
                break

        if in_share_section and not share_section_ended and line:
            parts = line.split()
            if len(parts) >= 2:
                share_name = parts[0]
                share_type = parts[1]

                if not share_name.replace('_', '').replace('-', '').isalnum():
                    op.output.print_if_verbose(f"Skipping invalid share name format: {share_name}")
                    continue

                if not share_name.endswith('$') and share_type == "Disk":
                    shares.append(share_name)
                    op.output.print_if_verbose(f"Added share: {share_name}")
                elif share_name.endswith('$'):
                    op.output.print_if_verbose(f"Skipped administrative share: {share_name}")
                else:
                    op.output.print_if_verbose(f"Skipped non-disk share: {share_name} ({share_type})")

    op.output.print_if_verbose(f"Parsed {len(shares)} valid shares from smbclient output")
    return shares
