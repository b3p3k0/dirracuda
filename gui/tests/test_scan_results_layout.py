"""Real layout regression for the previously withdrawn 1x1 centering bug."""
import tkinter as tk
import pytest
from gui.components.scan_results_dialog import ScanResultsDialog
from gui.tests.dialog_audit.inspect import measure


@pytest.fixture
def root(monkeypatch):
    monkeypatch.setattr("gui.components.scan_results_dialog.remember_window_position",lambda *a: None)
    try: root=tk.Tk()
    except tk.TclError: pytest.skip("Requires display")
    root.geometry("800x250+100+100");root.update()
    original_scale=root.tk.call("tk","scaling")
    yield root
    root.tk.call("tk","scaling",original_scale)
    root.destroy()
    import gc
    gc.collect()


@pytest.mark.parametrize("scale",[96/72,144/72])
@pytest.mark.parametrize("status",["completed","interrupted","failed","unknown"])
def test_results_size_and_controls(root,scale,status):
    root.tk.call("tk","scaling",scale)
    d=ScanResultsDialog(root,{"status":status,"shares_found":25,"end_time":"2026-10-03T12:00:00"})
    root.update()
    assert (d.dialog.winfo_width(),d.dialog.winfo_height())==(575,725)
    assert d.close_button.winfo_ismapped()
    assert not measure(d.dialog,("Close","Scan Summary","Details"))["findings"]
    assert root.grab_current() is None


def test_long_error_scrolls_and_footer_survives_resize(root):
    d=ScanResultsDialog(root,{"status":"failed","error":"A long error message. " * 150})
    d.dialog.geometry("500x400");root.update()
    assert d.body.scrollbar.winfo_ismapped()
    assert d.close_button.winfo_ismapped()
    d.body.canvas.yview_moveto(1);root.update()
    assert d.body.canvas.yview()[1] == 1.0
    assert not measure(d.dialog,("Close",))["findings"]
