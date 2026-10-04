"""Nested actions and transient progress surfaces for the layout audit."""
from types import SimpleNamespace
from unittest.mock import Mock


def register(add, cases, scenario_type):
    rows=[
        ("scan-output","components.dashboard_scan_output_dialog","ensure_scan_output_dialog",("Copy All",)),
        ("shodan-key","components.dashboard_shodan","prompt_for_shodan_api_key",("Cancel",)),
        ("stop-scan","dashboard.scan_controls","_show_stop_confirmation",("Stop Now",)),
        ("fetch-progress","components.dashboard_batch_ops","run_background_fetch",("Preparing",)),
        ("probe-progress","components.dashboard_batch_ops","execute_batch_probe",("Cancel",)),
        ("extract-progress","components.dashboard_batch_ops","execute_batch_extract",("Cancel",)),
        ("smb-browser","browsers.smb_browser","SmbBrowserWindow._build_window",()),
        ("ftp-browser","browsers.core","UnifiedBrowserCore._build_window",()),
        ("http-browser","browsers.core","UnifiedBrowserCore._build_window",()),
        ("large-file","browsers.smb_browser","SmbBrowserWindow._show_size_warning_dialog",("OK","Ignore Once")),
        ("large-image","browsers.smb_browser","SmbBrowserWindow._confirm_image_oversize",("OK","Cancel")),
        ("server-detail","components.server_list_window.details","show_server_detail_popup",()),
        ("probe-detail-config","components.server_list_window.details","_open_probe_dialog",("Start Probe","Cancel")),
        ("filters","components.server_list_window.filter_dropdown","FilterDropdown.open",("Favorites only","Has notes")),
        ("notes-tooltip","components.server_list_window.window","ServerListWindow._show_notes_tooltip",("Example",)),
        ("add-record","components.server_list_window.actions.batch_operations","ServerListWindowBatchOperationsMixin._show_add_record_dialog",("Save","Cancel")),
        ("record-notice","components.server_list_window.actions.batch_operations","ServerListWindowBatchOperationsMixin._maybe_show_reddit_promotion_notice",("OK",)),
        ("batch-probe-config","components.server_list_window.actions.batch_operations","ServerListWindowBatchOperationsMixin._prompt_probe_batch_settings",("Start","Cancel")),
        ("filter-template","components.server_list_window.actions.templates","ServerListWindowTemplateMixin._on_save_filter_template._prompt_overwrite_choice",("Update","New Template","Cancel")),
        ("export-progress","components.server_list_window.export","export_servers_to_format",("Preparing export",)),
        ("subset-export-confirm","components.server_list_window.export","save_hosts_to_database",("Save","Cancel","Include saved credentials")),
        ("subset-export-progress","components.server_list_window.export","_run_subset_export",("Cancel",)),
        ("import-progress","components.data_import_dialog","DataImportDialog._import_data",("Starting import",)),
    ]
    for row in rows: add(*row)
    cases.append(scenario_type("backend-setup","dirracuda:show_smbseek_setup_dialog",("Cancel",)))
    return {r[0] for r in rows}|{"backend-setup"}


def build(name, env):
    import tkinter as tk
    from gui.utils.style import get_theme
    root,theme,settings=env.root,get_theme(),env.settings
    noop=lambda *a,**kw:None
    server={"ip_address":"192.0.2.1","country":"Example","host_type":"S","auth_method":"anonymous","accessible_shares":"public","accessible_shares_list":"public","status":"active"}
    if name=="backend-setup":
        from gui.utils.dirracuda_loader import load_dirracuda_module
        return load_dirracuda_module().show_smbseek_setup_dialog(root,str(env.paths.repo_root))
    if name=="scan-output":
        from gui.components.dashboard_scan_output_dialog import show_scan_output_dialog
        dash=env.dash
        for key in ("_copy_log_output","_scroll_log_to_latest","_update_log_autoscroll_state","_configure_log_tags","_render_log_placeholder"):
            setattr(dash,key,noop)
        dash.log_bg_color=theme.colors["log_bg"];dash.log_fg_color=theme.colors["log_fg"]
        return show_scan_output_dialog(dash,protocol="SMB",country="US")
    if name=="shodan-key":
        from gui.components.dashboard_shodan import prompt_for_shodan_api_key
        return prompt_for_shodan_api_key(env.dash)
    if name=="stop-scan":
        from gui.dashboard.scan_controls import _show_stop_confirmation
        return _show_stop_confirmation(env.dash)
    if name in {"fetch-progress","probe-progress","extract-progress"}:
        import gui.components.dashboard
        from gui.components import dashboard_batch_ops as m
        # No workers run; the progress window remains in its initial state.
        if name=="fetch-progress": return m.run_background_fetch(env.dash,"Preparation","Preparing selected hosts…",noop)
        return getattr(m,"execute_batch_probe" if name=="probe-progress" else "execute_batch_extract")(env.dash,[server],allow_insecure_tls=False)
    if name in {"smb-browser","ftp-browser","http-browser"}:
        from importlib import import_module
        proto=name.split('-')[0]
        m=import_module("gui.browsers."+proto+"_browser")
        cls=getattr(m,{"smb":"SmbBrowserWindow","ftp":"FtpBrowserWindow","http":"HttpBrowserWindow"}[proto])
        kwargs=dict(config_path=env.config,db_reader=env.db,theme=theme,settings_manager=settings)
        if proto=="smb": kwargs.update(shares=["public","documents"],auth_method="anonymous")
        return cls(root,"192.0.2.1",**kwargs)
    if name in {"large-file","large-image"}:
        from gui.browsers.smb_browser import SmbBrowserWindow
        obj=SmbBrowserWindow.__new__(SmbBrowserWindow);obj.window=root;obj.theme=theme
        return getattr(obj,"_show_size_warning_dialog" if name=="large-file" else "_confirm_image_oversize")("example-file.txt",15000000,5)
    if name in {"server-detail","probe-detail-config"}:
        from gui.components.server_list_window import details
        if name=="server-detail": return details.show_server_detail_popup(root,server,theme,settings_manager=settings)
        return details._open_probe_dialog(root,server,tk.Text(root),tk.StringVar(),{},settings,theme,None)
    if name=="filters":
        from gui.components.server_list_window.filter_dropdown import FilterDropdown,QUICK_FILTERS
        obj=FilterDropdown(root,theme,{key:tk.BooleanVar(value=False) for key,_ in QUICK_FILTERS},noop)
        obj.button.pack();root.update();obj.open();return obj
    if name=="notes-tooltip":
        from gui.components.server_list_window.window import ServerListWindow
        obj=ServerListWindow.__new__(ServerListWindow);obj.window=root;obj.theme=theme;obj._notes_tooltip=None
        return obj._show_notes_tooltip("Example note\nSecond line",100,100)
    if name in {"add-record","record-notice","batch-probe-config"}:
        from gui.components.server_list_window.actions.batch_operations import ServerListWindowBatchOperationsMixin as cls
        obj=cls();obj.window=root;obj.theme=theme;obj.settings_manager=settings
        if name=="add-record": return obj._show_add_record_dialog(prefill=server)
        if name=="record-notice": return obj._maybe_show_reddit_promotion_notice()
        return obj._prompt_probe_batch_settings(15)
    if name=="filter-template":
        from gui.components.server_list_window.actions.templates import ServerListWindowTemplateMixin as cls
        obj=cls();obj.window=root;obj.filter_template_store=Mock();obj.filter_template_var=tk.StringVar(value="Audit template")
        obj.FILTER_TEMPLATE_PLACEHOLDER="Select";obj._selected_filter_template_slug="audit"
        return obj._on_save_filter_template()
    if name=="export-progress":
        from gui.components.server_list_window import export
        env.patch("gui.components.server_list_window.export.filedialog.asksaveasfilename",return_value=str(env.paths.data_dir/"fixture.csv"))
        engine=Mock()
        def export_data(**kwargs):
            for child in root.winfo_children():
                if isinstance(child,tk.Toplevel): env.capture(child)
            return {"success":True,"records_exported":1,"file_size":0,"format":"csv"}
        engine.export_data.side_effect=export_data
        env.patch("gui.utils.safe_messagebox.showinfo",return_value=None)
        return export.export_servers_to_format([server],"selected","csv",root,theme,engine)
    if name in {"subset-export-confirm","subset-export-progress"}:
        from gui.components.server_list_window import export
        filename=str(env.paths.data_dir/"fixture-subset.db")
        active_db_path=str(env.paths.main_db_file)
        if name=="subset-export-confirm":
            env.patch("gui.components.server_list_window.export.filedialog.asksaveasfilename",return_value=filename)
            return export.save_hosts_to_database(root,[{**server,"row_key":"S:1"}],theme,active_db_path)
        # Workers are inert, so the progress dialog stays open for capture.
        return export._run_subset_export(root,filename,["S:1"],True,theme,active_db_path)
    if name=="import-progress":
        from gui.components.data_import_dialog import DataImportDialog
        obj=DataImportDialog(root,env.db);obj.selected_file=str(env.paths.data_dir/"fixture.csv");obj.preview_data={"total_records":1}
        obj.validation_result={"valid":True};obj.data_type_var.set("servers");obj.import_mode_var.set("merge")
        def import_data(**kwargs):
            for child in obj.dialog.winfo_children():
                if isinstance(child,tk.Toplevel):env.capture(child)
            return {"success":True,"records_processed":1,"records_inserted":1,"records_updated":0,"records_skipped":0}
        obj.import_engine=SimpleNamespace(import_data=import_data)
        env.patch("gui.utils.safe_messagebox.askyesno",return_value=True)
        env.patch("gui.utils.safe_messagebox.showinfo",return_value=None)
        return obj._import_data()
    raise ValueError(name)
