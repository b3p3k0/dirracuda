"""
Download/listing pipeline methods for FileBrowserWindow.

Private mixin — do not instantiate directly.
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.smb_browser import SMBNavigator
from shared.quarantine import build_quarantine_path, log_quarantine_event
from gui.components.batch_extract_dialog import BatchExtractSettingsDialog, NO_EXTENSION_TOKEN


def _format_file_size(size_bytes: int) -> str:
    """Convert bytes to human-readable format (e.g., '1.6 MB')."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(size_bytes)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[unit_index]}"


class _FileBrowserDownloadMixin:
    """Mixin — download/listing pipeline methods only; no ``__init__``."""

    def _on_cancel(self) -> None:
        """Cancel in-flight and queued downloads and return UI to idle state."""
        self.navigator.cancel()
        if self.download_cancel_event:
            self.download_cancel_event.set()
        self._set_status("Cancellation requested…")
        self.btn_cancel.configure(state=tk.DISABLED)

    def _prompt_extract_options(self, target_count: int) -> Optional[Dict[str, Any]]:
        """Use the shared batch extract dialog for folder downloads."""
        config_path = self.config_path
        if self.settings_manager:
            # Prefer user-set backend config path if available
            cfg_override = self.settings_manager.get_setting('backend.config_path', None)
            if cfg_override:
                config_path = cfg_override
        dialog_config = BatchExtractSettingsDialog(
            parent=self.window,
            theme=self.theme,
            settings_manager=self.settings_manager,
            config_path=config_path,
            config_editor_callback=None,
            mode="on-demand",
            target_count=target_count
        ).show()

        if not dialog_config:
            return None

        # Persist legacy folder limits for continuity
        limits = {
            "max_depth": int(dialog_config.get("max_directory_depth", 0)),
            "max_files": int(dialog_config.get("max_files_per_target", 0)),
            "max_total_mb": int(dialog_config.get("max_total_size_mb", 0)),
            "max_file_mb": int(dialog_config.get("max_file_size_mb", 0)),
        }
        self._persist_folder_limit_defaults(limits)

        # Add extension filter settings for directory expansion
        limits.update({
            "extension_mode": dialog_config.get("extension_mode", "download_all"),
            "included_extensions": [ext.lower() for ext in dialog_config.get("included_extensions", [])],
            "excluded_extensions": [ext.lower() for ext in dialog_config.get("excluded_extensions", [])],
        })
        return limits

    def _start_list_thread(self, path: str) -> None:
        # Mark busy before the worker thread starts to block re-entrant navigation.
        self._set_busy(True)

        def worker():
            try:
                self._ensure_connected()
                result = self.navigator.list_dir(path)
                self._safe_after(0, lambda: self._populate_entries(result, path))
            except Exception as e:
                # On navigation failure, revert to last committed path and surface context.
                self._safe_after(0, lambda err=e, attempted=path: self._handle_list_error(attempted, err))
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.list_thread = threading.Thread(target=worker, daemon=True)
        self.list_thread.start()

    def _start_download_thread(self, files_with_mtime: List[Tuple[str, Optional[float], int]], remote_dirs: List[str], folder_limits: Optional[Dict[str, Any]]) -> None:
        """
        Stream directory expansion into a bounded queue and start downloads immediately.
        """
        def worker():
            try:
                self._set_busy(True)
                self._ensure_connected()
                worker_count = max(1, min(3, int(self.workers_var.get() or self.download_workers)))
                large_threshold_bytes = max(1, int(self.large_mb_var.get() or self.download_large_mb)) * 1024 * 1024
                dest_dir = build_quarantine_path(
                    self.ip_address,
                    self.current_share,
                    base_path=self.config.get("quarantine_root"),
                )

                q_small: queue.Queue = queue.Queue(maxsize=200)
                q_large: queue.Queue = queue.Queue(maxsize=200)
                expand_errors: List[Tuple[str, str]] = []
                errors: List[Tuple[str, str]] = []
                completed = 0
                total_enqueued = 0
                done_enumerating = threading.Event()
                cancel_event = threading.Event()
                self.download_cancel_event = cancel_event

                limits = folder_limits or {}
                max_files = limits.get("max_files", 0)
                max_total_mb = limits.get("max_total_mb", 0)
                max_file_mb = limits.get("max_file_mb", 0)
                max_total_bytes = max_total_mb * 1024 * 1024 if max_total_mb else 0
                max_file_bytes = max_file_mb * 1024 * 1024 if max_file_mb else 0
                bytes_enqueued = 0

                def enqueue_file(path: str, mtime: Optional[float], size: int) -> bool:
                    nonlocal total_enqueued, bytes_enqueued
                    if cancel_event.is_set():
                        return False
                    if max_file_bytes and size > max_file_bytes:
                        expand_errors.append((path, "skipped: exceeds per-file limit"))
                        return True
                    if max_total_bytes and (bytes_enqueued + size) > max_total_bytes:
                        expand_errors.append((path, "total size limit reached"))
                        return False
                    if max_files and total_enqueued >= max_files:
                        expand_errors.append((path, "file limit reached"))
                        return False
                    target_q = q_large if (size and size > large_threshold_bytes) else q_small
                    while not cancel_event.is_set():
                        try:
                            target_q.put((path, mtime, size), timeout=0.5)
                            break
                        except queue.Full:
                            continue
                    total_enqueued += 1
                    bytes_enqueued += size
                    return True

                def producer():
                    try:
                        # Seed initial explicit files
                        for remote_path, mtime, size in files_with_mtime:
                            if cancel_event.is_set():
                                break
                            enqueue_file(remote_path, mtime, size or 0)

                        if remote_dirs and folder_limits:
                            self._safe_after(0, lambda: self._set_status("Enumerating selected folders..."))
                            enumerated = 0
                            stack: List[Tuple[str, int]] = [(d, 0) for d in remote_dirs]
                            max_depth = limits.get("max_depth", 0)
                            extension_mode = limits.get("extension_mode", "download_all")
                            included_ext = [ext.lower() for ext in limits.get("included_extensions", [])]
                            excluded_ext = [ext.lower() for ext in limits.get("excluded_extensions", [])]
                            while stack and not cancel_event.is_set():
                                current_path, depth = stack.pop()
                                if max_depth and depth > max_depth:
                                    continue
                                try:
                                    entries = self.navigator.list_dir(current_path)
                                except Exception as exc:
                                    expand_errors.append((current_path, str(exc)))
                                    continue
                                for entry in entries.entries:
                                    if cancel_event.is_set():
                                        break
                                    name = entry.name
                                    rel = self._join_path(current_path, name)
                                    if entry.is_dir:
                                        stack.append((rel, depth + 1))
                                        continue
                                    size = entry.size or 0
                                    if not self._should_include_extension(name, limits.get("extension_mode", "download_all"),
                                                                          [ext.lower() for ext in limits.get("included_extensions", [])],
                                                                          [ext.lower() for ext in limits.get("excluded_extensions", [])]):
                                        continue
                                    if not enqueue_file(rel, entry.modified_time, size):
                                        break
                                    enumerated += 1
                                    if enumerated % 50 == 0:
                                        self._safe_after(0, lambda count=enumerated, qs=q_small.qsize()+q_large.qsize(): self._set_status(f"Enumerating... {count} files queued ({qs} ready)"))
                    finally:
                        done_enumerating.set()

                def consumer(target_q: queue.Queue):
                    nonlocal completed
                    last_status = {"ts": 0}
                    # Per-worker navigator to avoid sharing SMBConnection across threads
                    worker_nav = SMBNavigator(
                        allow_smb1=bool(self.config.get("allow_smb1", True)),
                        connect_timeout=float(self.config.get("connect_timeout_seconds", 8)),
                        request_timeout=float(self.config.get("request_timeout_seconds", 10)),
                        max_entries=int(self.config.get("max_entries_per_dir", 5000)),
                        max_depth=int(self.config.get("max_depth", 12)),
                        max_path_length=int(self.config.get("max_path_length", 240)),
                        download_chunk_mb=int(self.config.get("download_chunk_mb", 4)),
                    )
                    try:
                        worker_nav.connect(
                            host=self.ip_address,
                            share=self.current_share,
                            username=self.username,
                            password=self.password,
                        )
                    except Exception as exc:
                        errors.append(("", f"Worker connect failed: {exc}"))
                        return

                    while not (done_enumerating.is_set() and q_small.empty() and q_large.empty()) and not cancel_event.is_set():
                        try:
                            item = target_q.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        if cancel_event.is_set():
                            target_q.task_done()
                            break
                        remote_path, mtime, _size = item
                        self._safe_after(0, lambda rp=remote_path, c=completed, t=lambda: max(total_enqueued, completed + 1): self._set_status(f"Downloading {rp} ({c+1}/{t()})"))
                        try:
                            last_update = {"ts": 0}

                            def _progress(bytes_written: int, _total_unused: Optional[int]) -> None:
                                now = time.time()
                                if cancel_event.is_set():
                                    worker_nav.cancel()
                                    return
                                if now - last_update["ts"] < 0.2:
                                    return
                                last_update["ts"] = now
                                human = _format_file_size(bytes_written)
                                self._safe_after(0, lambda rp=remote_path, c=completed, h=human: self._set_status(
                                    f"Downloading {rp} ({c+1}/{max(total_enqueued, completed+1)}) – {h} (workers {self.workers_var.get()})"))

                            result = worker_nav.download_file(
                                remote_path,
                                dest_dir,
                                preserve_structure=True,
                                mtime=mtime,
                                progress_callback=_progress
                            )
                            try:
                                host_dir = Path(dest_dir).parent.parent  # host/date/share
                                log_quarantine_event(host_dir, f"downloaded {self.current_share}{remote_path} -> {result.saved_path}")
                            except Exception:
                                pass
                            completed += 1
                        except Exception as e:
                            friendly = self._map_download_error(e)
                            errors.append((remote_path, friendly))
                        finally:
                            target_q.task_done()
                    # If cancel requested mid-transfer, ensure connection is closed promptly
                    if cancel_event.is_set():
                        try:
                            worker_nav.cancel()
                        except Exception:
                            pass
                    try:
                        worker_nav.disconnect()
                    except Exception:
                        pass

                producer_thread = threading.Thread(target=producer, daemon=True)
                consumer_threads = []
                for _ in range(worker_count):
                    consumer_threads.append(threading.Thread(target=consumer, args=(q_small,), daemon=True))
                # Single worker for large files
                consumer_threads.append(threading.Thread(target=consumer, args=(q_large,), daemon=True))

                producer_thread.start()
                for t in consumer_threads:
                    t.start()

                producer_thread.join()
                for t in consumer_threads:
                    t.join()

                if cancel_event.is_set():
                    self._safe_after(0, lambda: self._set_status("Download cancelled."))
                else:
                    summary_msg = f"Downloaded {completed}/{max(total_enqueued, completed)} file(s)"
                    total_errors = len(errors) + len(expand_errors)
                    if total_errors:
                        summary_msg += f" ({total_errors} failed)"
                    self._safe_after(0, lambda: self._set_status(summary_msg))
                    if completed > 0:
                        self._safe_after(0, self._handle_extracted_success)
                    if total_errors:
                        combined = errors + expand_errors
                        err_text = "\n".join(f"{p}: {err}" for p, err in combined[:5])
                        self._safe_after(0, lambda: messagebox.showwarning("Download issues", err_text, parent=self.window) if self._window_alive() else None)
                    else:
                        self._safe_after(0, lambda: messagebox.showinfo("Download complete", summary_msg, parent=self.window) if self._window_alive() else None)
            except Exception as e:
                self._safe_after(0, lambda err=e: self._set_status(f"Download failed: {err}"))
                self._safe_after(0, lambda err=e: messagebox.showerror("Download failed", str(err), parent=self.window) if self._window_alive() else None)
            finally:
                self._safe_after(0, lambda: self._set_busy(False))

        self.download_thread = threading.Thread(target=worker, daemon=True)
        self.download_thread.start()

    def _expand_directories(self, dirs: List[str], limits: Dict[str, Any]) -> Tuple[List[Tuple[str, Optional[float]]], int, List[Tuple[str, str]]]:
        max_depth = limits.get("max_depth", 0)
        max_files = limits.get("max_files", 0)
        max_total_mb = limits.get("max_total_mb", 0)
        max_file_mb = limits.get("max_file_mb", 0)
        extension_mode = limits.get("extension_mode", "download_all")
        included_ext = [ext.lower() for ext in limits.get("included_extensions", [])]
        excluded_ext = [ext.lower() for ext in limits.get("excluded_extensions", [])]

        expanded: List[Tuple[str, Optional[float]]] = []  # (path, mtime) tuples
        errors: List[Tuple[str, str]] = []
        skipped = 0
        total_bytes = 0
        enumerated = 0

        stack: List[Tuple[str, int]] = [(d, 0) for d in dirs]

        while stack:
            current_path, depth = stack.pop()
            if max_depth and depth > max_depth:
                continue
            try:
                entries = self.navigator.list_dir(current_path)
            except Exception as exc:
                errors.append((current_path, str(exc)))
                continue
            for entry in entries.entries:
                name = entry.name
                rel = self._join_path(current_path, name)
                if entry.is_dir:
                    stack.append((rel, depth + 1))
                    continue

                size = entry.size or 0
                if max_file_mb and size > max_file_mb * 1024 * 1024:
                    skipped += 1
                    continue
                if max_total_mb:
                    if (total_bytes + size) > max_total_mb * 1024 * 1024:
                        errors.append((rel, "total size limit reached"))
                        return expanded, skipped, errors

                if not self._should_include_extension(name, extension_mode, included_ext, excluded_ext):
                    skipped += 1
                    continue

                expanded.append((rel, entry.modified_time))
                total_bytes += size
                enumerated += 1
                if enumerated % 50 == 0:
                    self._safe_after(0, lambda count=enumerated: self._set_status(f"Enumerating... {count} files queued"))
                if max_files and len(expanded) >= max_files:
                    return expanded, skipped, errors

        return expanded, skipped, errors

    def _should_include_extension(self, name: str, mode: str, included: List[str], excluded: List[str]) -> bool:
        """Determine if a file should be included based on extension filters."""
        if mode == "download_all":
            return True
        ext = Path(name).suffix.lower()
        token = ext if ext else NO_EXTENSION_TOKEN.lower()
        if mode == "allow_only":
            return token in included
        if mode == "deny_only":
            return token not in excluded
        return True

    def _handle_extracted_success(self) -> None:
        """Invoke callback/DB flag when a download succeeds."""
        if callable(self.on_extracted):
            try:
                self.on_extracted(self.ip_address)
            except Exception:
                pass
            return
        if self.db_reader:
            try:
                self.db_reader.upsert_extracted_flag(self.ip_address, True)
            except Exception:
                pass

    @staticmethod
    def _map_download_error(exc: Exception) -> str:
        """Translate low-level download errors into user-friendly messages."""
        text = str(exc)
        lowered = text.lower()
        if "protocolid" in lowered or "unpacked data doesn't match" in lowered:
            return "Unexpected SMB response from server (often happens with large or partial transfers). File not saved."
        if "timed out" in lowered or "timeout" in lowered:
            return "Download timed out. Try again or reduce file size."
        if "cancelled" in lowered:
            return "Download cancelled."
        return text
