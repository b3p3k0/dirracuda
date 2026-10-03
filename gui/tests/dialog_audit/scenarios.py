"""Explicit deterministic fixtures for application-owned windows.

Each case names the production construction site and controls that must exist.
Notebook pages and scroll positions are visited by the runner, not mocked away.
"""
from dataclasses import dataclass
from importlib import import_module
from types import SimpleNamespace


@dataclass(frozen=True)
class Scenario:
    name: str
    site: str
    expected: tuple[str, ...]
    variant: str = ""


CASES = []


def add(name, module, owner, expected=(), variant=""):
    CASES.append(Scenario(name, f"gui/{module.replace('.', '/')}.py:{owner}", tuple(expected), variant))


for status in ("completed", "interrupted", "failed", "unknown", "long-error"):
    add(f"scan-results-{status}", "components.scan_results_dialog", "ScanResultsDialog._create_dialog", ("Scan Summary", "Details", "Close"), status)
for protocol in ("ftp", "http", "multi", "searxng", "reddit"):
    add(f"scan-results-{protocol}", "components.scan_results_dialog", "ScanResultsDialog._create_dialog", ("Scan Summary", "Details", "Close"), protocol)
for name, module, owner, expected in [
    ("start-scan", "unified_scan_layout", "build_dialog", ("Start", "Cancel")),
    ("scan-help", "unified_scan_dialog", "UnifiedScanDialog._show_cost_estimate_help_dialog", ("Close",)),
    ("scan-ftp", "ftp_scan_dialog", "FtpScanDialog._create_dialog", ("Start", "Cancel")),
    ("scan-http", "http_scan_dialog", "HttpScanDialog._create_dialog", ("Start", "Cancel")),
    ("config", "app_config_dialog", "AppConfigDialog._create_dialog", ("Save", "Cancel")),
    ("database-tools", "db_tools_dialog", "DBToolsDialog._create_dialog", ("Close",)),
    ("database-setup", "database_setup_dialog", "DatabaseSetupDialog._create_dialog", ("Exit",)),
    ("database-import", "data_import_dialog", "DataImportDialog._create_dialog", ("Cancel",)),
    ("config-editor", "config_editor_window", "SimpleConfigEditorWindow._create_window", ("Save",)),
    ("accessories", "experimental_features_dialog", "ExperimentalFeaturesDialog._build", ("Close",)),
    ("dorkbook", "dorkbook_window", "DorkbookWindow.__init__", ("Add",)),
    ("dorkbook-edit", "dorkbook_window", "_EntryEditorDialog.__init__", ("Save", "Cancel")),
    ("dorkbook-delete", "dorkbook_window", "_DeleteConfirmDialog.__init__", ("Delete", "Cancel")),
    ("keymaster", "keymaster_window", "KeymasterWindow.__init__", ("Add",)),
    ("keymaster-edit", "keymaster_window", "_KeyEditorDialog.__init__", ("Save", "Cancel")),
    ("keymaster-delete", "keymaster_window", "_SimpleDeleteConfirmDialog.__init__", ("Delete", "Cancel")),
    ("probe-config", "scan_preflight", "ProbeConfigDialog.show", ("Save & Continue", "Abort Scan")),
    ("preflight-summary", "scan_preflight", "SummaryDialog.show", ("Start",)),
    ("extract-settings", "batch_extract_dialog", "BatchExtractSettingsDialog.show", ("Start", "Cancel")),
    ("extension-editor", "batch_extract_dialog", "ExtensionEditorDialog.show", ("Save", "Cancel")),
    ("running-tasks", "running_tasks_window", "RunningTasksWindow._build", ("Cancel Task",)),
    ("batch-status", "pry_status_dialog", "BatchStatusDialog.__init__", ("Cancel",)),
    ("batch-summary", "batch_summary_dialog", "show_batch_summary_dialog", ("Close",)),
    ("clamav-results", "clamav_results_dialog", "_build_dialog", ("Close",)),
    ("manual", "help_manual_dialog", "UserManualDialog._build_window", ("Close",)),
    ("file-viewer", "file_viewer_window", "FileViewerWindow._build_window", ("Close",)),
    ("image-viewer", "image_viewer_window", "ImageViewerWindow._build_window", ("Close",)),
    ("analyst-report", "analyst_report_window", "AnalystReportWindow._build", ("Export",)),
    ("database-menu", "dashboard_database", "open_db_surface", ("View Servers", "DB Tools")),
    ("legacy-menu", "dashboard_experimental", "open_sidecar_legacy_db", ()),
    ("about", "dashboard_about", "open_about_dialog", ("Close",)),
    ("server-list", "server_list_window.window", "ServerListWindow._create_window", ()),
    ("ftp-picker", "ftp_server_picker", "FtpServerPickerDialog._build_dialog", ("Close",)),
]:
    add(name, "components." + module, owner, expected)
for provider, module, cls, method in [
    ("reddit", "reddit_browser_window", "RedditBrowserWindow", "_show_target_details"),
    ("search", "se_dork_browser_window", "SeDorkBrowserWindow", "_show_result_details"),
    ("censys", "censys_browser_window", "CensysBrowserWindow", "_show_result_details"),
]:
    add(provider + "-browser", "components." + module, cls + ".__init__")
    add(provider + "-detail", "components." + module, cls + "." + method)
for name, module, owner, expected in [
    ("sherlock-settings", "sherlock_tab", "open_sherlock_settings_window", ("Close",)),
    ("sherlock-manager", "sherlock_pattern_manager", "open_pattern_manager", ("Save", "Close")),
    ("sherlock-pattern", "sherlock_pattern_manager", "open_pattern_dialog", ("OK", "Cancel")),
    ("sherlock-category", "sherlock_category_actions", "prompt_category_name", ("Cancel",)),
    ("analyst-profiles", "analyst_profile_editor", "ProfileEditorDialog.__init__", ("Add", "Close")),
    ("analyst-profile-edit", "analyst_profile_editor", "_ProfileFormDialog.__init__", ("Save", "Cancel")),
    ("analyst-consent", "analyst_egress_consent", "_ConsentDialog.__init__", ("Cancel",)),
    ("analyst-advanced", "analyst_tab", "AnalystTab._open_advanced", ()),
    ("analyst-export", "analyst_export_dialog", "open_export_dialog", ("Export", "Cancel")),
    ("webui-credentials", "webui_tab", "WebUITab._open_credentials_dialog", ("Save Credentials", "Close")),
    ("webui-config", "webui_tab", "WebUITab._open_config_dialog", ("Save", "Close")),
]:
    add(name, "components.experimental_features." + module, owner, expected)
add("yes-cancel", "utils.safe_messagebox", "askyescancel", ("Yes", "Cancel"))


from .extra_scenarios import register, build as build_extra
EXTRA = register(add, CASES, Scenario)

# States with different content allocation share the real production builder.
ALIASES = {
    "scan-results-empty": "scan-results-completed",
    "start-scan-default": "start-scan",
    "batch-summary-empty": "batch-summary",
    "clamav-results-populated": "clamav-results",
    "analyst-report-populated": "analyst-report",
    "accessories-dismissed": "accessories",
    "server-list-advanced": "server-list",
}
for name, original in ALIASES.items():
    base = next(case for case in CASES if case.name == original)
    CASES.append(Scenario(name, base.site, base.expected, name))


def build(case, env):
    if case.name in EXTRA:
        return build_extra(case.name, env)
    import tkinter as tk
    from gui.utils.style import get_theme
    root, theme, settings = env.root, get_theme(), env.settings
    module = import_module(case.site.split(":")[0][:-3].replace("/", "."))
    name = ALIASES.get(case.name, case.name)
    noop = lambda *a, **kw: None
    if name.startswith("scan-results-"):
        variant = case.variant
        results = dict(status=variant if variant in {"completed", "interrupted", "failed", "unknown"} else "completed",
                       protocol=variant if variant in {"ftp", "http", "multi", "searxng", "reddit"} else "smb",
                       hosts_scanned=100, accessible_hosts=15, shares_found=25,
                       end_time="2026-10-03T10:30:00", duration_seconds=120, protocols=[])
        if variant == "multi": results["protocols"] = ["smb", "ftp", "http"]
        if variant == "long-error": results.update(status="failed", error="Backend not found. " * 100)
        if variant == "scan-results-empty":
            results.update(hosts_scanned=0, accessible_hosts=0, shares_found=0)
        return module.ScanResultsDialog(root, results)
    if name in {"start-scan", "scan-help", "scan-smb", "scan-ftp", "scan-http"}:
        from gui.components.unified_scan_dialog import UnifiedScanDialog
        classes = {"scan-smb": ("scan_dialog", "ScanDialog"), "scan-ftp": ("ftp_scan_dialog", "FtpScanDialog"), "scan-http": ("http_scan_dialog", "HttpScanDialog")}
        if name in classes:
            mod, cls = classes[name]
            return getattr(import_module("gui.components." + mod), cls)(root, env.config, config_editor_callback=noop, scan_start_callback=noop, settings_manager=settings)
        if case.name != "start-scan-default":
            for key in ("provider_shodan", "provider_searxng", "provider_reddit"):
                settings.set_setting("unified_scan_dialog." + key, True)
        dialog = UnifiedScanDialog(root, env.config, noop, settings_manager=settings)
        if name == "scan-help": dialog._show_cost_estimate_help_dialog()
        return dialog
    if name == "config":
        env.patch("gui.components.app_config_dialog.AppConfigDialog._validate_smbseek_path", return_value={"valid": True, "message": "Audit backend"})
        return module.AppConfigDialog(root, settings_manager=settings)
    if name == "database-tools": return module.DBToolsDialog(root, str(env.paths.main_db_file))
    if name == "database-setup": return module.DatabaseSetupDialog(root, str(env.paths.main_db_file), env.config)
    if name == "database-import": return module.DataImportDialog(root, env.db)
    if name == "config-editor": return module.SimpleConfigEditorWindow(root, env.config)
    if name == "accessories":
        if case.name == "accessories-dismissed":
            settings.set_setting("experimental.warning_dismissed", True)
        return module.ExperimentalFeaturesDialog(root, env.context(), settings)
    if name == "dorkbook": return module.DorkbookWindow(root, settings_manager=settings, db_path=env.paths.dorkbook_db_file, scan_query_config_path=env.config)
    if name == "dorkbook-edit": return module._EntryEditorDialog(root, theme, title="Edit Dork", nickname="Example listing", query='intitle:"index of /"', editing=True)
    if name == "dorkbook-delete": return module._DeleteConfirmDialog(root, theme, prompt_text="Delete Example listing?")
    if name == "keymaster": return module.KeymasterWindow(root, settings_manager=settings, db_path=env.paths.keymaster_db_file, config_path=env.config)
    if name == "keymaster-edit": return module._KeyEditorDialog(root, theme, title="Edit key", label="Audit fixture", api_key="test-key")
    if name == "keymaster-delete": return module._SimpleDeleteConfirmDialog(root, theme, label_text="Audit fixture")
    if name == "probe-config": return module.ProbeConfigDialog(root, theme, settings).show()
    if name == "preflight-summary": return module.SummaryDialog(root, theme, ["SMB, FTP and HTTP", "Bulk probe enabled", "Extraction enabled"], "100 hosts selected").show()
    if name == "extract-settings": return module.BatchExtractSettingsDialog(root, theme, settings, env.config, target_count=15).show()
    if name == "extension-editor": return module.ExtensionEditorDialog(root, theme, env.paths.config_file, ["txt", "pdf"], ["exe"]).show()
    if name == "running-tasks":
        from gui.utils.running_tasks import RunningTaskRegistry
        obj = module.RunningTasksWindow(root, theme, RunningTaskRegistry()); obj.show(); return obj
    if name == "batch-status": return module.BatchStatusDialog(root, theme, title="Probe progress", fields={"Host": "192.0.2.1", "Progress": "3 / 15"}, on_cancel=noop, total=15)
    if name == "batch-summary":
        rows = [] if case.name.endswith("empty") else [{"ip_address":"192.0.2.1", "status":"success", "message":"Probe completed"}]
        return module.show_batch_summary_dialog(parent=root, theme=theme, job_type="probe", results=rows, show_protocol=True, show_stats=True, show_risk=True)
    if name == "clamav-results":
        rows = []
        if case.name.endswith("populated"):
            rows = [{"ip_address": "192.0.2.1", "clamav": {
                "enabled": True, "files_scanned": 40, "clean": 10, "infected": 25, "errors": 5,
                "infected_items": [{"path": f"/fixture/file-{n}.txt", "signature": "Fixture.Signature", "moved_to": "/quarantine/fixture"} for n in range(25)],
                "error_items": [{"path": "/fixture/unreadable.txt", "error": "Fixture read error"}],
            }}]
        return module.show_clamav_results_dialog(parent=root, theme=theme, results=rows, on_mute=noop, wait=False, modal=False)
    if name == "manual": return module.UserManualDialog(root)
    if name == "file-viewer": return module.FileViewerWindow(root, "example.txt", b"Example content\n" * 100, 1600)
    if name == "image-viewer":
        import io
        from PIL import Image
        data=io.BytesIO(); Image.new("RGB", (320,240), "steelblue").save(data, format="PNG")
        return module.ImageViewerWindow(root, "example.png", data.getvalue(), 1000000)
    if name == "analyst-report":
        obj = module.AnalystReportWindow(root, db_path=env.paths.analyst_db_file)
        if case.name.endswith("populated"):
            from .fixtures import analyst_report
            obj._show_report(analyst_report(), changed=True)
        return obj
    if name == "database-menu": return module.open_db_surface(env.dash)
    if name == "legacy-menu": return module.open_sidecar_legacy_db(env.dash)
    if name == "about": return module.open_about_dialog(env.dash)
    if name == "server-list":
        obj = module.ServerListWindow(root, env.db, settings_manager=settings)
        if case.name.endswith("advanced"):
            obj._set_mode(True)
        return obj
    if name == "ftp-picker": return module.FtpServerPickerDialog(root, env.db, config_path=env.config, settings_manager=settings)
    if name.startswith(("reddit-", "search-", "censys-")):
        cls = {"reddit":"RedditBrowserWindow", "search":"SeDorkBrowserWindow", "censys":"CensysBrowserWindow"}[name.split("-")[0]]
        obj = getattr(module, cls)(root, db_path=env.paths.experimental_dir / (name + ".db"), settings_manager=settings)
        if name.endswith("detail"):
            row={"id":1, "url":"http://192.0.2.1/", "ip_address":"192.0.2.1", "title":"Example listing", "protocol":"http"}
            getattr(obj, "_show_target_details" if name.startswith("reddit") else "_show_result_details")(row)
        return obj
    if name == "sherlock-settings": return module.open_sherlock_settings_window(root, settings)
    if name.startswith("sherlock-"):
        from gui.components.experimental_features.sherlock_tab import SherlockTab
        tab = SherlockTab(root, env.context())
        if name == "sherlock-manager": return module.open_pattern_manager(tab)
        if name == "sherlock-pattern": return module.open_pattern_dialog(tab)
        return module.prompt_category_name(tab, title="Add Category", prompt="Category name:")
    if name == "analyst-profiles": return module.ProfileEditorDialog(root, db_path=env.paths.analyst_db_file)
    if name == "analyst-profile-edit": return module._ProfileFormDialog(root, title="Add model server")
    if name == "analyst-consent":
        profile=SimpleNamespace(name="Lab model", endpoint_url="http://192.0.2.1:11434", host="192.0.2.1", address_class=SimpleNamespace(value="private"))
        return module._ConsentDialog(root, profile=profile, model_name="fixture-model")
    if name.startswith("analyst-"):
        from gui.components.experimental_features.analyst_tab import AnalystTab
        tab = AnalystTab(root, env.context())
        if name == "analyst-advanced": return tab._open_advanced()
        tab._runs.insert("", "end", iid="audit-run", values=("Example",)); tab._runs.selection_set("audit-run")
        return module.open_export_dialog(tab)
    if name.startswith("webui-"):
        tab=module.WebUITab(root, env.context())
        return getattr(tab, "_open_credentials_dialog" if name.endswith("credentials") else "_open_config_dialog")()
    if name == "yes-cancel": return module.askyescancel("Confirm operation", "Continue with the selected items?", parent=root)
    raise ValueError(name)
