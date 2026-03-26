import concurrent.futures
import socket
import threading
import time
import uuid
from contextlib import redirect_stderr
from datetime import datetime
from io import StringIO
from typing import Set, List, Dict, Optional
import random

from .smb_support import Connection, Session, SMBException


def check_smbclient_availability() -> bool:
    """
    Backward-compatible transport availability check.

    This now validates pure-Python SMB adapter availability instead of checking
    for a system smbclient binary.
    """
    try:
        from shared.smb_adapter import SMBAdapter  # noqa: F401
        return True
    except Exception:
        return False


def get_smb_adapter(op):
    """Return or lazily create an SMBAdapter bound to current operation."""
    adapter = getattr(op, "_smb_adapter", None)
    if adapter is not None:
        return adapter

    from shared.smb_adapter import SMBAdapter

    timeout = op.config.get_connection_timeout()
    adapter = SMBAdapter(timeout_seconds=timeout)
    setattr(op, "_smb_adapter", adapter)
    return adapter


def throttled_auth_wait(op) -> None:
    """
    Enhanced thread-safe rate limiting for discovery authentication attempts.
    """
    if not op.config.get_discovery_smart_throttling():
        basic_throttled_auth_wait(op)
        return

    with op._auth_rate_lock:
        current_time = time.monotonic()

        if op._last_auth_attempt == 0:
            op._last_auth_attempt = current_time
            return

        base_delay = op.config.get_rate_limit_delay()
        active_threads = threading.active_count() - 1  # Exclude main thread

        effective_delay = base_delay / max(1, active_threads * 0.7)
        effective_delay = max(0.5, effective_delay)

        jitter = effective_delay * 0.2 * (random.random() - 0.5)
        final_delay = effective_delay + jitter

        time_elapsed = current_time - op._last_auth_attempt

        if time_elapsed < final_delay:
            sleep_time = final_delay - time_elapsed
            time.sleep(sleep_time)

        op._last_auth_attempt = time.monotonic()


def basic_throttled_auth_wait(op) -> None:
    """
    Basic thread-safe rate limiting (fallback when smart throttling disabled).
    """
    with op._auth_rate_lock:
        current_time = time.monotonic()

        if op._last_auth_attempt == 0:
            op._last_auth_attempt = current_time
            return

        time_elapsed = current_time - op._last_auth_attempt
        rate_delay = op.config.get_rate_limit_delay()

        if time_elapsed < rate_delay:
            sleep_time = rate_delay - time_elapsed
            time.sleep(sleep_time)

        op._last_auth_attempt = time.monotonic()


def test_single_host_concurrent(op, ip: str, country=None) -> Dict:
    """
    Thread-safe wrapper for test_single_host that returns structured results.
    """
    try:
        throttled_auth_wait(op)
        result = test_single_host(op, ip, country)

        if result:
            return {"result": result, "success": True, "failed": False, "metadata": {}}
        else:
            return {"result": None, "success": False, "failed": True, "metadata": {}}

    except Exception as e:
        return {
            "ip": ip,
            "error": str(e),
            "success": False,
            "failed": True,
            "result": None,
            "metadata": {}
        }


def test_smb_authentication(op, ip_addresses: Set[str], country=None) -> List[Dict]:
    """
    Test SMB authentication on IP addresses with configurable concurrency.
    """
    if not ip_addresses:
        return []

    total_hosts = len(ip_addresses)
    op.output.info(f"Testing SMB authentication on {total_hosts} hosts...")

    max_concurrent_hosts = op.config.get_max_concurrent_discovery_hosts()
    max_workers = get_optimal_workers(op, total_hosts, max_concurrent_hosts)

    ip_list = list(ip_addresses)

    if max_workers == 1:
        return test_smb_authentication_sequential(op, ip_list, country)

    successful_hosts = []
    results_by_index = [None] * total_hosts

    completed_count = 0
    progress_success_count = 0
    progress_failed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        per_future_timeout = op.config.get_connection_timeout() + op.config.get("connection", "port_check_timeout", 10) + 5

        future_to_index = {
            executor.submit(test_single_host_concurrent, op, ip, country): i
            for i, ip in enumerate(ip_list)
        }

        op.output.print_if_verbose(f"Started {len(future_to_index)} concurrent authentication tasks with {max_workers} workers")

        for future in concurrent.futures.as_completed(future_to_index, timeout=per_future_timeout * total_hosts):
            index = future_to_index[future]
            ip = ip_list[index]

            try:
                result_data = future.result(timeout=per_future_timeout)
                results_by_index[index] = result_data

                if "error" in result_data:
                    op.output.print_if_verbose(f"Authentication failed for {result_data['ip']}: {result_data['error']}")

            except concurrent.futures.TimeoutError:
                timeout_result = {
                    "ip": ip,
                    "error": f"Timeout after {per_future_timeout}s",
                    "success": False,
                    "failed": True,
                    "result": None
                }
                results_by_index[index] = timeout_result
                op.output.print_if_verbose(f"Authentication timeout for {ip} after {per_future_timeout}s")

            except Exception as e:
                error_result = {
                    "ip": ip,
                    "error": str(e),
                    "success": False,
                    "failed": True,
                    "result": None
                }
                results_by_index[index] = error_result
                op.output.print_if_verbose(f"Authentication error for {ip}: {e}")

            completed_count += 1
            if index < len(results_by_index) and results_by_index[index] and results_by_index[index].get("success"):
                progress_success_count += 1
            else:
                progress_failed_count += 1

            active_threads = sum(1 for f in future_to_index if not f.done())
            if completed_count % 10 == 0 or completed_count == 1 or completed_count == total_hosts:
                report_concurrent_progress(
                    op,
                    completed_count,
                    total_hosts,
                    progress_success_count,
                    progress_failed_count,
                    active_threads
                )

    success_count = 0
    failed_count = 0

    for i, result_data in enumerate(results_by_index):
        if result_data and result_data["success"] and result_data["result"]:
            successful_hosts.append(result_data["result"])
            success_count += 1
            op.output.print_if_verbose(f"  ✓ {ip_list[i]}: {result_data['result']['auth_method']}")
        else:
            failed_count += 1

    op.stats['successful_auth'] = success_count
    op.stats['failed_auth'] = failed_count
    op.stats['total_processed'] = total_hosts

    success_percent = int((success_count / total_hosts) * 100) if total_hosts else 0
    op.output.info(
        f"📊 Authentication complete: {total_hosts} hosts | "
        f"Success: {success_count}, Failed: {failed_count} ({success_percent}%)"
    )

    return successful_hosts


def test_smb_authentication_sequential(op, ip_list: List[str], country=None) -> List[Dict]:
    """
    Sequential SMB authentication testing (preserved for max_concurrent_hosts=1).
    """
    successful_hosts = []
    total_hosts = len(ip_list)

    for i, ip in enumerate(ip_list, 1):
        if i % 25 == 0 or i == 1 or i == total_hosts:
            progress_pct = (i / total_hosts) * 100
            success_count = len(successful_hosts)
            failed_count = i - 1 - success_count
            processed = success_count + failed_count
            success_percent = int((success_count / processed) * 100) if processed else 0
            op.output.info(
                f"📊 Progress: {i}/{total_hosts} ({progress_pct:.1f}%) | "
                f"Success: {success_count}, Failed: {failed_count} ({success_percent}%)"
            )

        op.output.print_if_verbose(f"[{i}/{total_hosts}] Testing {ip}...")

        result = test_single_host(op, ip, country)
        if result:
            successful_hosts.append(result)
            op.output.print_if_verbose(f"  ✓ {ip}: {result['auth_method']}")

        if i < total_hosts:
            time.sleep(op.config.get_rate_limit_delay())

    op.stats['successful_auth'] = len(successful_hosts)
    op.stats['failed_auth'] = total_hosts - len(successful_hosts)
    op.stats['total_processed'] = total_hosts

    return successful_hosts


def test_single_host(op, ip: str, country=None) -> Optional[Dict]:
    """
    Test SMB authentication on a single host.
    """
    if not check_port(op, ip, 445):
        return None

    auth_method = test_smb_alternative(op, ip)
    if auth_method:
        metadata = op.shodan_host_metadata.get(ip, {})
        country_name = metadata.get('country_name') or country or 'Unknown'
        country_code = metadata.get('country_code')

        return {
            'ip_address': ip,
            'country': country_name,
            'country_code': country_code,
            'auth_method': auth_method,
            'timestamp': datetime.now().isoformat(),
            'status': 'accessible'
        }

    return None


def check_port(op, ip: str, port: int) -> bool:
    """Check if port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(op.config.get("connection", "port_check_timeout", 10))
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except:
        return False


def test_smb_auth(op, ip: str, username: str, password: str) -> bool:
    """
    Test SMB authentication with security hardening based on cautious_mode.
    """
    conn_uuid = str(uuid.uuid4())
    connection = None
    session = None

    require_signing = op.cautious_mode
    require_encryption = False
    dialects = None

    if op.cautious_mode:
        try:
            from smbprotocol.connection import Dialect
            dialects = [Dialect.SMB_2_0_2, Dialect.SMB_2_1, Dialect.SMB_3_0_2, Dialect.SMB_3_1_1]
        except ImportError:
            dialects = None
            op.output.print_if_verbose("SMB dialect restriction unavailable - using library defaults with signing")

    try:
        stderr_buffer = StringIO()
        with redirect_stderr(stderr_buffer):
            try:
                connection = Connection(conn_uuid, ip, 445, require_signing=require_signing, dialects=dialects)
            except TypeError:
                connection = Connection(conn_uuid, ip, 445, require_signing=require_signing)
                if op.cautious_mode:
                    op.output.print_if_verbose("SMB dialect restriction not supported by library - enforcing signing only")

            connection.connect(timeout=op.config.get_connection_timeout())

            session = Session(
                connection,
                username=username,
                password=password,
                require_encryption=require_encryption,
                auth_protocol="ntlm"
            )
            session.connect()

            return True

    except SMBException as e:
        if op.cautious_mode:
            error_msg = str(e).lower()
            if 'signing' in error_msg or 'unsigned' in error_msg:
                op.output.print_if_verbose(f"Host {ip} requires unsigned SMB - rejected in cautious mode")
            elif 'smb' in error_msg and ('version' in error_msg or 'dialect' in error_msg):
                op.output.print_if_verbose(f"Host {ip} requires SMB1 or unsupported protocol - rejected in cautious mode")
        return False
    except Exception:
        return False
    finally:
        op._connection_pool.return_connection(ip, connection, session)


def test_smb_alternative(op, ip: str) -> Optional[str]:
    """
    Pure-Python auth probe with caching.

    This keeps the original method contract but routes probing through the SMB
    adapter (`smbprotocol` first, `impacket` fallback in legacy mode).
    """
    if not check_smbclient_availability():
        return None

    if ip in op._smbclient_auth_cache:
        return op._smbclient_auth_cache[ip]

    adapter = get_smb_adapter(op)
    result = adapter.probe_authentication(
        ip,
        cautious_mode=op.cautious_mode,
        timeout_seconds=op.config.get_connection_timeout(),
    )
    method = result.get("auth_method") if result.get("success") else None

    op._smbclient_auth_cache[ip] = method
    return method


def get_optimal_workers(op, total_hosts: int, max_concurrent: int) -> int:
    """
    Scale workers based on workload size and system capacity.
    """
    if total_hosts <= 10:
        return min(3, max_concurrent, total_hosts)
    worker_cap = op.config.get_max_worker_cap()
    return min(max_concurrent, total_hosts, worker_cap)


def report_concurrent_progress(op, completed: int, total: int,
                               success_count: int, failed_count: int,
                               active_threads: int):
    """
    Enhanced progress reporting with concurrency awareness.
    """
    progress_pct = (completed / total) * 100
    success_rate = (success_count / max(1, completed)) * 100

    op.output.info(
        f"📊 Progress: {completed}/{total} ({progress_pct:.1f}%) | "
        f"Success: {success_count}, Failed: {failed_count} ({success_rate:.0f}%) | "
        f"Active: {active_threads} threads"
    )
