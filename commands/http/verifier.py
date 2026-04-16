"""
Pure HTTP verification functions for Card 4.

All functions are stdlib-only (no requests dependency).
Mirrors the structure of commands/ftp/verifier.py.
"""
from __future__ import annotations

import re
import socket
import ssl
import urllib.error
import urllib.request
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Stage 1 — TCP port reachability
# ---------------------------------------------------------------------------

def port_check(ip: str, port: int, timeout: float = 5.0) -> Tuple[bool, str]:
    """
    TCP reachability test for ip:port.

    Returns (True, '') on success.
    Returns (False, 'timeout') on socket.timeout / TimeoutError.
    Returns (False, 'connect_fail') on any other OSError.

    CRITICAL: socket.timeout is an OSError subclass — caught before OSError.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, ""
    except socket.timeout:
        return False, "timeout"
    except TimeoutError:
        return False, "timeout"
    except OSError:
        return False, "connect_fail"


# ---------------------------------------------------------------------------
# Stage 2 — HTTP request
# ---------------------------------------------------------------------------

def try_http_request(
    ip: str,
    port: int,
    scheme: str,
    allow_insecure_tls: bool = True,
    timeout: float = 10.0,
    path: str = "/",
    request_host: Optional[str] = None,
) -> Tuple[int, str, bool, str]:
    """
    Perform a single HTTP GET request.

    Returns (status_code, body_text, tls_verified, reason):
      status_code  — HTTP status integer (0 on network failure)
      body_text    — response body as str ('' on failure)
      tls_verified — True when TLS cert was successfully verified
      reason       — '' on success; failure taxonomy code on failure

    TLS logic:
      scheme='http'  → no SSL; tls_verified=False always
      scheme='https' + allow_insecure_tls=True  → ctx with check_hostname=False,
                                                   verify_mode=CERT_NONE;
                                                   tls_verified=False
      scheme='https' + allow_insecure_tls=False → default ctx (verify cert);
                                                   tls_verified=True on success,
                                                   reason='tls_error' on ssl.SSLError

    Exception catch order matters:
      urllib.error.HTTPError (subclass of URLError) must be caught first.
      socket.timeout / TimeoutError must be caught before OSError.
    """
    authority = str(ip or "").strip()
    normalized_path = str(path or "/").strip() or "/"
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    url = f"{scheme}://{authority}:{port}{normalized_path}"

    ctx: ssl.SSLContext | None = None
    tls_verified = False

    if scheme == "https":
        if allow_insecure_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            tls_verified = False
        else:
            ctx = ssl.create_default_context()
            # default ctx verifies cert; tls_verified=True on success
            tls_verified = True  # optimistic; cleared on SSLError

    headers = {"User-Agent": "Mozilla/5.0"}
    host_header = str(request_host or "").strip()
    if host_header:
        if (
            ":" not in host_header
            and not (host_header.startswith("[") and host_header.endswith("]"))
        ):
            default_port = 443 if scheme == "https" else 80
            if port != default_port:
                host_header = f"{host_header}:{port}"
        headers["Host"] = host_header

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(1024 * 256).decode("utf-8", errors="replace")
            return resp.status, body, tls_verified, ""

    except urllib.error.HTTPError as exc:
        # Server responded with an error status — body may still be useful
        try:
            body = exc.read(1024 * 256).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body, False, ""

    except ssl.SSLError:
        # Only raised when allow_insecure_tls=False and cert verification fails
        return 0, "", False, "tls_error"

    except socket.timeout:
        return 0, "", False, "timeout"

    except TimeoutError:
        return 0, "", False, "timeout"

    except urllib.error.URLError as exc:
        reason_str = str(exc.reason) if exc.reason else str(exc)
        reason_lower = reason_str.lower()
        if any(kw in reason_lower for kw in ("name or service", "name resolution", "nodename")):
            return 0, "", False, "dns_fail"
        if "too many redirects" in reason_lower or "redirect" in reason_lower:
            return 0, "", False, "redirect_loop"
        if isinstance(exc.reason, OSError):
            return 0, "", False, "connect_fail"
        return 0, "", False, "connect_fail"

    except Exception:
        return 0, "", False, "connect_fail"


# ---------------------------------------------------------------------------
# Stage 2 — Index page validation
# ---------------------------------------------------------------------------

def validate_index_page(body: str, status_code: int) -> bool:
    """
    Return True only when body is a genuine Apache/nginx directory listing.

    Criteria (ALL must pass):
      1. status_code == 200
      2. <title>Index of ... (case-insensitive) OR <title>Directory listing
      3. At least one <a href="..."> anchor present
    """
    if status_code != 200:
        return False
    if not re.search(r"<title[^>]*>\s*(?:index of|directory listing)", body, re.IGNORECASE):
        return False
    if not re.search(r'<a\s+href=["\']', body, re.IGNORECASE):
        return False
    return True


# ---------------------------------------------------------------------------
# Stage 2 — Entry counting
# ---------------------------------------------------------------------------

def count_dir_entries(body: str) -> Tuple[int, int, List[str]]:
    """
    Parse an Apache/nginx directory listing HTML body and count entries.

    Returns (dir_count, file_count, dir_paths):
      dir_count  — number of <a href> values ending in '/' (excluding '../')
      file_count — number of <a href> values NOT ending in '/' and not sort links
      dir_paths  — list of relative href paths for subdirectory links

    Raises ValueError on parse exception — caller records reason='parse_error'.
    Returns (0, 0, []) for a valid but empty listing — caller records success with 0 counts.
    """
    try:
        hrefs = re.findall(r'<a\s+href=["\']([^"\']+)["\']', body, re.IGNORECASE)
        dir_count = 0
        file_count = 0
        dir_paths: List[str] = []

        for href in hrefs:
            # Skip parent directory link
            if href == "../" or href == "..":
                continue
            # Skip sort query links (e.g., ?C=N&O=D)
            if href.startswith("?"):
                continue
            # Skip absolute paths that escape the listing (e.g., /icons/)
            if href.startswith("/") and not href.startswith("//"):
                continue
            # Skip protocol-relative and external links
            if "://" in href:
                continue

            if href.endswith("/"):
                dir_count += 1
                dir_paths.append(href)
            else:
                file_count += 1

        return dir_count, file_count, dir_paths

    except Exception as exc:
        raise ValueError(f"Failed to parse directory listing: {exc}") from exc


# ---------------------------------------------------------------------------
# Stage 2 — One-level subdir fetching
# ---------------------------------------------------------------------------

def fetch_subdir_entries(
    ip: str,
    port: int,
    scheme: str,
    subdir_path: str,
    allow_insecure_tls: bool = True,
    timeout: float = 8.0,
) -> Tuple[int, int]:
    """
    Fetch one subdirectory and return its (dir_count, file_count).

    Returns (0, 0) on any failure — never raises.
    Only counts if the subdir response passes validate_index_page().
    """
    try:
        status_code, body, _tls_verified, _reason = try_http_request(
            ip, port, scheme, allow_insecure_tls, timeout, path=f"/{subdir_path.lstrip('/')}"
        )
        if not validate_index_page(body, status_code):
            return 0, 0
        dir_count, file_count, _ = count_dir_entries(body)
        return dir_count, file_count
    except Exception:
        return 0, 0
