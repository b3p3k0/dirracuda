"""
Pure-Python SMB transport adapter.

This module centralizes SMB transport operations so discovery/access code can
switch from shell-based smbclient usage to Python libraries without changing
its result contracts.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


AUTH_METHODS_DEFAULT: Tuple[Tuple[str, str, str], ...] = (
    ("Anonymous", "", ""),
    ("Guest/Blank", "guest", ""),
    ("Guest/Guest", "guest", "guest"),
)


SMB_STATUS_HINTS: Dict[str, str] = {
    "NT_STATUS_ACCESS_DENIED": "Access denied - insufficient permissions",
    "NT_STATUS_BAD_NETWORK_NAME": "Share not found or unavailable",
    "NT_STATUS_LOGON_FAILURE": "Authentication failed",
    "NT_STATUS_ACCOUNT_DISABLED": "User account is disabled",
    "NT_STATUS_ACCOUNT_LOCKED_OUT": "User account is locked out",
    "NT_STATUS_PASSWORD_EXPIRED": "Password has expired",
    "NT_STATUS_CONNECTION_REFUSED": "Connection refused by server",
    "NT_STATUS_HOST_UNREACHABLE": "Host is unreachable",
    "NT_STATUS_NETWORK_UNREACHABLE": "Network is unreachable",
    "NT_STATUS_IO_TIMEOUT": "Connection timed out",
    "NT_STATUS_PIPE_NOT_AVAILABLE": "Named pipe not available",
    "NT_STATUS_PIPE_BROKEN": "Named pipe broken",
    "NT_STATUS_OBJECT_NAME_NOT_FOUND": "Object or path not found",
    "NT_STATUS_SHARING_VIOLATION": "File is in use by another process",
    "NT_STATUS_INSUFFICIENT_RESOURCES": "Insufficient server resources",
}


class SMBAdapter:
    """High-level SMB adapter for auth/share operations."""

    _STATUS_RE = re.compile(r"(NT_STATUS_[A-Z0-9_]+|STATUS_[A-Z0-9_]+)")

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = max(1, int(timeout_seconds))

    def probe_authentication(
        self,
        ip_address: str,
        *,
        cautious_mode: bool,
        auth_methods: Optional[Sequence[Tuple[str, str, str]]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Probe SMB auth methods and return normalized result metadata.

        Strategy:
        1) Try smbprotocol in all modes (strict cautious semantics).
        2) If not cautious, fallback to impacket so SMB1 discovery remains possible.
        """
        methods = tuple(auth_methods or AUTH_METHODS_DEFAULT)
        timeout = self._resolve_timeout(timeout_seconds)
        attempts: List[Dict[str, Any]] = []

        for method_name, username, password in methods:
            success, error, status_code = self._try_smbprotocol_auth(
                ip_address=ip_address,
                username=username,
                password=password,
                require_signing=cautious_mode,
                timeout=timeout,
            )
            attempts.append(
                {
                    "method": method_name,
                    "backend": "smbprotocol",
                    "success": success,
                    "status_code": status_code,
                    "error": error,
                }
            )
            if success:
                return {
                    "ip_address": ip_address,
                    "success": True,
                    "auth_method": method_name,
                    "backend": "smbprotocol",
                    "status_code": "OK",
                    "error": None,
                    "attempts": attempts,
                }

        if cautious_mode:
            return {
                "ip_address": ip_address,
                "success": False,
                "auth_method": None,
                "backend": None,
                "status_code": attempts[-1]["status_code"] if attempts else "ERROR",
                "error": attempts[-1]["error"] if attempts else "Authentication failed",
                "attempts": attempts,
            }

        for method_name, username, password in methods:
            success, error, status_code = self._try_impacket_auth(
                ip_address=ip_address,
                username=username,
                password=password,
                allow_smb1=True,
                timeout=timeout,
            )
            attempts.append(
                {
                    "method": method_name,
                    "backend": "impacket",
                    "success": success,
                    "status_code": status_code,
                    "error": error,
                }
            )
            if success:
                return {
                    "ip_address": ip_address,
                    "success": True,
                    "auth_method": method_name,
                    "backend": "impacket",
                    "status_code": "OK",
                    "error": None,
                    "attempts": attempts,
                }

        return {
            "ip_address": ip_address,
            "success": False,
            "auth_method": None,
            "backend": None,
            "status_code": attempts[-1]["status_code"] if attempts else "ERROR",
            "error": attempts[-1]["error"] if attempts else "Authentication failed",
            "attempts": attempts,
        }

    def list_shares(
        self,
        ip_address: str,
        *,
        username: str,
        password: str,
        cautious_mode: bool,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Enumerate shares with normalized share metadata."""
        timeout = self._resolve_timeout(timeout_seconds)

        try:
            rows = self._query_shares_impacket(
                ip_address=ip_address,
                username=username,
                password=password,
                cautious_mode=cautious_mode,
                timeout=timeout,
            )
            shares = [self._normalize_share_row(row) for row in rows]
            return {
                "success": True,
                "backend": "impacket",
                "shares": shares,
                "status_code": "OK",
                "error": None,
            }
        except Exception as exc:
            message = str(exc)
            status = self._coerce_status_code(message)
            error = self._friendly_error_from_status(status)
            if status == "ERROR" and message:
                error = message
            return {
                "success": False,
                "backend": "impacket",
                "shares": [],
                "status_code": status,
                "error": error,
            }

    def probe_share_read(
        self,
        ip_address: str,
        *,
        share_name: str,
        username: str,
        password: str,
        cautious_mode: bool,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Check whether share root can be listed with current credentials."""
        timeout = self._resolve_timeout(timeout_seconds)

        try:
            names = self._query_share_entries_impacket(
                ip_address=ip_address,
                share_name=share_name,
                username=username,
                password=password,
                cautious_mode=cautious_mode,
                timeout=timeout,
            )
            visible = [name for name in names if name not in (".", "..")]
            accessible = len(visible) > 0
            return {
                "share_name": share_name,
                "accessible": accessible,
                "backend": "impacket",
                "entry_count": len(visible),
                "status_code": "OK" if accessible else "ACCESS_DENIED",
                "error": None if accessible else "Access denied or empty share",
            }
        except Exception as exc:
            message = str(exc)
            status = self._coerce_status_code(message)
            error = self._friendly_error_from_status(status)
            if status == "ERROR" and message:
                error = message
            return {
                "share_name": share_name,
                "accessible": False,
                "backend": "impacket",
                "entry_count": 0,
                "status_code": status,
                "error": error,
            }

    def _query_shares_impacket(
        self,
        *,
        ip_address: str,
        username: str,
        password: str,
        cautious_mode: bool,
        timeout: int,
    ) -> Iterable[Dict[str, Any]]:
        conn = self._open_impacket_session(
            ip_address=ip_address,
            username=username,
            password=password,
            allow_smb1=not cautious_mode,
            timeout=timeout,
        )
        try:
            return conn.listShares()
        finally:
            self._close_impacket_session(conn)

    def _query_share_entries_impacket(
        self,
        *,
        ip_address: str,
        share_name: str,
        username: str,
        password: str,
        cautious_mode: bool,
        timeout: int,
    ) -> List[str]:
        conn = self._open_impacket_session(
            ip_address=ip_address,
            username=username,
            password=password,
            allow_smb1=not cautious_mode,
            timeout=timeout,
        )
        try:
            entries = conn.listPath(share_name, "*")
            names: List[str] = []
            for entry in entries:
                if hasattr(entry, "get_longname"):
                    names.append(str(entry.get_longname()))
                else:
                    names.append(str(entry))
            return names
        finally:
            self._close_impacket_session(conn)

    def _open_impacket_session(
        self,
        *,
        ip_address: str,
        username: str,
        password: str,
        allow_smb1: bool,
        timeout: int,
    ):
        from impacket.smbconnection import SMBConnection, SMB_DIALECT  # type: ignore
        from impacket.smb3structs import SMB2_DIALECT_002  # type: ignore

        preferred_dialect = None if allow_smb1 else SMB2_DIALECT_002
        conn = SMBConnection(
            ip_address,
            ip_address,
            sess_port=445,
            timeout=timeout,
            preferredDialect=preferred_dialect,
        )
        conn.login(username, password)

        if not allow_smb1 and conn.getDialect() == SMB_DIALECT:
            self._close_impacket_session(conn)
            raise RuntimeError("SMB1 negotiated while SMB2+ was required")

        return conn

    def _close_impacket_session(self, conn: Any) -> None:
        if conn is None:
            return
        try:
            conn.logoff()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    def _try_smbprotocol_auth(
        self,
        *,
        ip_address: str,
        username: str,
        password: str,
        require_signing: bool,
        timeout: int,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        connection = None
        session = None
        try:
            from smbprotocol.connection import Connection  # type: ignore
            from smbprotocol.session import Session  # type: ignore

            dialects = self._smbprotocol_cautious_dialects() if require_signing else None

            try:
                connection = Connection(
                    str(uuid.uuid4()),
                    ip_address,
                    445,
                    require_signing=require_signing,
                    dialects=dialects,
                )
            except TypeError:
                connection = Connection(
                    str(uuid.uuid4()),
                    ip_address,
                    445,
                    require_signing=require_signing,
                )

            connection.connect(timeout=timeout)

            session = Session(
                connection,
                username=username,
                password=password,
                require_encryption=False,
                auth_protocol="ntlm",
            )
            session.connect()
            return True, None, None

        except Exception as exc:
            message = str(exc)
            return False, message, self._extract_status_code(message)
        finally:
            if session is not None:
                try:
                    session.disconnect()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.disconnect(True)
                except Exception:
                    try:
                        connection.disconnect()
                    except Exception:
                        pass

    def _try_impacket_auth(
        self,
        *,
        ip_address: str,
        username: str,
        password: str,
        allow_smb1: bool,
        timeout: int,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        conn = None
        try:
            conn = self._open_impacket_session(
                ip_address=ip_address,
                username=username,
                password=password,
                allow_smb1=allow_smb1,
                timeout=timeout,
            )
            return True, None, None
        except Exception as exc:
            message = str(exc)
            return False, message, self._extract_status_code(message)
        finally:
            self._close_impacket_session(conn)

    def _smbprotocol_cautious_dialects(self) -> Optional[List[Any]]:
        try:
            from smbprotocol.connection import Dialect as _Dialect  # type: ignore
        except Exception:
            try:
                from smbprotocol.connection import Dialects as _Dialect  # type: ignore
            except Exception:
                return None

        dialect_values: List[Any] = []
        for name in ("SMB_2_0_2", "SMB_2_1", "SMB_3_0_2", "SMB_3_1_1"):
            if hasattr(_Dialect, name):
                dialect_values.append(getattr(_Dialect, name))

        return dialect_values or None

    def _resolve_timeout(self, timeout_seconds: Optional[int]) -> int:
        if timeout_seconds is None:
            return self.timeout_seconds
        return max(1, int(timeout_seconds))

    def _normalize_share_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        name = self._trim_trailing_nul(str(row.get("shi1_netname", "")))
        comment = self._trim_trailing_nul(str(row.get("shi1_remark", "")))
        share_type = int(row.get("shi1_type", 0))

        return {
            "name": name,
            "comment": comment,
            "type": share_type,
            "is_disk": share_type == 0,
            "is_admin": name.endswith("$"),
        }

    @staticmethod
    def _trim_trailing_nul(value: str) -> str:
        return value.rstrip("\x00").strip()

    def _extract_status_code(self, message: str) -> Optional[str]:
        if not message:
            return None
        match = self._STATUS_RE.search(message.upper())
        if not match:
            return None
        status = match.group(1)
        if status.startswith("NT_STATUS_"):
            return status
        if status.startswith("STATUS_"):
            return f"NT_{status}"
        return status

    def _friendly_error_from_status(self, status_code: str) -> str:
        if not status_code:
            return "SMB protocol error"
        if status_code in SMB_STATUS_HINTS:
            return SMB_STATUS_HINTS[status_code]
        if status_code == "ACCESS_DENIED":
            return "Access denied - insufficient permissions"
        if status_code == "BAD_NETWORK_NAME":
            return "Share not found or unavailable"
        if status_code == "TIMEOUT":
            return "Connection timed out"
        return f"SMB protocol error ({status_code})"

    def _coerce_status_code(self, message: str) -> str:
        extracted = self._extract_status_code(message)
        if extracted:
            return extracted

        lower = (message or "").lower()
        if "timed out" in lower or "timeout" in lower:
            return "TIMEOUT"
        if "connection refused" in lower:
            return "NT_STATUS_CONNECTION_REFUSED"
        if "host unreachable" in lower:
            return "NT_STATUS_HOST_UNREACHABLE"
        if "network unreachable" in lower or "no route to host" in lower:
            return "NT_STATUS_NETWORK_UNREACHABLE"
        if "bad network name" in lower or "share not found" in lower:
            return "NT_STATUS_BAD_NETWORK_NAME"
        if "access denied" in lower:
            return "NT_STATUS_ACCESS_DENIED"
        if "logon failure" in lower or "authentication failed" in lower:
            return "NT_STATUS_LOGON_FAILURE"
        return "ERROR"


__all__ = [
    "AUTH_METHODS_DEFAULT",
    "SMB_STATUS_HINTS",
    "SMBAdapter",
]
