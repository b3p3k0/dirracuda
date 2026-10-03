"""Desktop shutdown with cancellation, bounded waiting, and honest reporting."""

import tkinter as tk


def close_application(app, *, messagebox, time, logger, get_tmpfs_runtime_state,
                      tmpfs_has_quarantined_files, cleanup_tmpfs_quarantine):
    if getattr(app, "_closing", False):
        return
    app._closing = True
    app._pending_tmpfs_startup_warning = None
    app._pending_tmpfs_warning_code = None
    dashboard = app.dashboard
    errors = []
    windows = list(app.drill_down_windows.values())
    # Keep real handles: registry entries can disappear before a worker exits.
    futures = [future for window in windows
               for job in getattr(window, "active_jobs", {}).values()
               for _, future in job.get("futures", [])]
    deliveries = [window._batch_delivery for window in windows
                  if getattr(window, "_batch_delivery", None) is not None]
    for delivery in deliveries:
        futures.extend(delivery.pending_futures())
    backends = [getattr(owner, "backend_interface", None)
                for owner in (app, app.scan_manager, dashboard)]
    processes = []

    def attempt(label, callback):
        try:
            return callback()
        except Exception:
            errors.append(label)
            logger.exception("Shutdown: %s; details follow", label)

    def active():
        for backend in backends:
            process = getattr(backend, "active_process", None)
            if process is not None and process not in processes:
                processes.append(process)
        tracked = False
        if dashboard and hasattr(dashboard, "has_active_or_queued_work"):
            tracked = bool(dashboard.has_active_or_queued_work())
        return bool(
            tracked or (app.scan_manager and app.scan_manager.is_scanning)
            or any(not future.done() for future in futures)
            or any(delivery.pending_futures() for delivery in deliveries)
            or any(process.poll() is None for process in processes)
        )

    def cancel():
        if dashboard and hasattr(dashboard, "request_cancel_active_or_queued_work"):
            dashboard.request_cancel_active_or_queued_work()
        elif app.scan_manager and app.scan_manager.is_scanning:
            app.scan_manager.interrupt_scan()

    def force():
        if dashboard and hasattr(dashboard, "force_terminate_active_work"):
            dashboard.force_terminate_active_work()
        elif app.scan_manager and app.scan_manager.is_scanning:
            app.scan_manager.interrupt_scan()
        for backend in backends:
            process = getattr(backend, "active_process", None)
            if process is not None and process.poll() is None:
                backend.terminate_current_operation()

    has_active_work = attempt("could not check running tasks", active)
    if has_active_work:
        response = messagebox.askyesno(
            "Tasks in Progress",
            "A scan is running or scans/tasks are queued.\n\n"
            "Stop all running and queued tasks and exit?",
            icon="warning", parent=app.root,
        )
        if not response:
            app._closing = False
            return
        attempt("could not request task cancellation", cancel)
        start = time.time()
        retried = False
        while time.time() - start < 6.0:
            try:
                if app.root and app.root.winfo_exists():
                    app.root.update_idletasks()
                    app.root.update()
            except tk.TclError:
                break
            still_active = attempt("could not check remaining tasks", active)
            if not still_active:
                break
            if not retried and time.time() - start >= 3.0:
                retried = True
                attempt("could not retry task cancellation", cancel)
                attempt("could not terminate the scan subprocess", force)
            time.sleep(0.10)
        if attempt("could not check remaining tasks", active):
            attempt("could not terminate the scan subprocess", force)

    try:
        if get_tmpfs_runtime_state().get("tmpfs_active") and tmpfs_has_quarantined_files():
            proceed = messagebox.askyesno(
                "In-Memory Quarantine Will Be Lost",
                "There are quarantined files stored in memory (tmpfs).\n\n"
                "Closing now will permanently delete them.\n\n"
                "Do you want to continue?",
                icon="warning", parent=app.root,
            )
            if not proceed:
                app._closing = False
                return
    except Exception:
        errors.append("quarantine state check failed")
        logger.exception("Shutdown: quarantine state check failed; details follow")

    attempt("configuration could not be saved", app.config.save_config)
    # Check before teardown removes registry entries. A stopped UI is not proof
    # of stopped work, and executor.shutdown(wait=False) does not join workers.
    remaining = attempt("could not verify task shutdown", active)
    for window in windows:
        close = getattr(window, "_close_window", None) or getattr(window, "destroy", None)
        if close:
            attempt("a child window could not close", close)
    if dashboard and hasattr(dashboard, "teardown_dashboard_monitors"):
        attempt("task monitors could not close", dashboard.teardown_dashboard_monitors)
    if app.db_reader:
        attempt("database cleanup failed", app.db_reader.clear_cache)
    if app.ui_dispatcher:
        attempt("interface dispatcher could not stop", app.ui_dispatcher.stop)
    cleanup = attempt("quarantine cleanup failed", cleanup_tmpfs_quarantine)
    if cleanup is not None and not cleanup.get("ok", False):
        errors.append("quarantine cleanup failed")
        logger.error("Shutdown: quarantine cleanup failed: %s", cleanup.get("message"))
    attempt("application window could not close", app.root.destroy)

    if remaining:
        logger.error(
            "Application window closed, but background work has not stopped; "
            "clean shutdown could not be confirmed."
        )
    elif (errors or any(delivery.failed or getattr(getattr(delivery, "dispatcher", None), "failed", False)
                        for delivery in deliveries)
          or getattr(app.ui_dispatcher, "failed", False)):
        logger.error("Application closed with errors; see the diagnostic details above.")
    elif has_active_work:
        logger.warning("Application closed during a task; tracked work stopped and cleanup completed.")
