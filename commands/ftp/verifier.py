"""
FTP host verification helpers (Card 4).

Three pure functions, no external dependencies beyond stdlib:
  port_check       — TCP reachability test
  try_anon_login   — FTP anonymous login attempt
  try_root_listing — FTP root directory listing attempt

Reason-code mapping is the single source of truth from the plan (Section 4):

  port_check:
    socket.timeout / TimeoutError → (False, 'timeout')
    OSError                       → (False, 'connect_fail')
    clean connect                 → (True,  '')

  try_anon_login:
    ftplib.error_perm             → (False, '', 'auth_fail')
    socket.timeout / TimeoutError → (False, '', 'timeout')
    EOFError                      → (False, '', 'auth_fail')
    any other Exception           → (False, '', 'auth_fail')
    success                       → (True,  banner, '')

  try_root_listing:
    ftplib.error_perm             → (False, 0, 'list_fail')
    socket.timeout / TimeoutError → (False, 0, 'timeout')
    EOFError                      → (False, 0, 'list_fail')
    any other Exception           → (False, 0, 'list_fail')
    success                       → (True,  count, '')

IMPORTANT: socket.timeout is an OSError subclass (Python 3.3+). In port_check,
(socket.timeout, TimeoutError) MUST be caught before OSError so that DROP-rule
timeouts receive 'timeout' and not 'connect_fail'.
"""
from __future__ import annotations

import ftplib
import socket
from typing import Tuple


def port_check(ip: str, port: int, timeout: float = 5.0) -> Tuple[bool, str]:
    """
    Test TCP reachability for ip:port.

    Returns (True, '') on success.
    Returns (False, 'timeout') when the connection times out (DROP rule).
    Returns (False, 'connect_fail') on any other OS-level error (RST, unreachable).
    """
    try:
        conn = socket.create_connection((ip, port), timeout)
        conn.close()
        return True, ""
    except (socket.timeout, TimeoutError):
        # Caught before OSError because socket.timeout is an OSError subclass.
        return False, "timeout"
    except OSError:
        return False, "connect_fail"


def try_anon_login(ip: str, port: int, timeout: float = 10.0) -> Tuple[bool, str, str]:
    """
    Attempt an anonymous FTP login to ip:port.

    Returns (ok, banner, reason):
      ok     — True on successful login
      banner — server greeting banner ('' on failure)
      reason — '' on success; 'auth_fail' or 'timeout' on failure
    """
    ftp = ftplib.FTP()
    try:
        ftp.connect(ip, port, timeout=timeout)
        banner = ftp.getwelcome()
        ftp.login()  # anonymous / '' by default
        try:
            ftp.quit()
        except Exception:
            pass
        return True, banner, ""
    except ftplib.error_perm:
        return False, "", "auth_fail"
    except (socket.timeout, TimeoutError):
        return False, "", "timeout"
    except EOFError:
        # Server closed connection before login completed — treat as rejection.
        return False, "", "auth_fail"
    except Exception:
        return False, "", "auth_fail"
    finally:
        try:
            ftp.close()
        except Exception:
            pass


def try_root_listing(ip: str, port: int, timeout: float = 15.0) -> Tuple[bool, int, str]:
    """
    Attempt an anonymous FTP login and root directory listing on ip:port.

    Returns (ok, count, reason):
      ok     — True when listing completed
      count  — number of entries in root directory (0 on failure or empty root)
      reason — '' on success; 'list_fail' or 'timeout' on failure
    """
    ftp = ftplib.FTP()
    try:
        ftp.connect(ip, port, timeout=timeout)
        ftp.login()
        entries = ftp.nlst()
        try:
            ftp.quit()
        except Exception:
            pass
        return True, len(entries), ""
    except ftplib.error_perm:
        return False, 0, "list_fail"
    except (socket.timeout, TimeoutError):
        return False, 0, "timeout"
    except EOFError:
        # Server closed connection before listing completed.
        return False, 0, "list_fail"
    except Exception:
        return False, 0, "list_fail"
    finally:
        try:
            ftp.close()
        except Exception:
            pass
