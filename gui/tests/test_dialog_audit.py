"""Prove the audit distinguishes broken geometry from intentional scrolling."""
import tkinter as tk
from tkinter import ttk
import pytest
from gui.tests.dialog_audit.inspect import measure
from gui.utils.scrollable_body import ScrollableBody


@pytest.fixture
def window():
    try:
        root=tk.Tk()
    except tk.TclError:
        pytest.skip("Requires Xvfb or a display")
    root.geometry("250x160+0+0")
    yield root
    root.destroy()
    import gc
    gc.collect()  # Tk callback cycles must be finalized on the Tk thread.


def test_detects_clipped_and_missing_controls(window):
    label=tk.Label(window,text="A very long required label " * 10)
    label.pack()
    button=tk.Button(window,text="Required action")
    button.place(x=0,y=300)
    window.update()
    findings=measure(window,("Absent control",))["findings"]
    assert any(f["kind"]=="missing" for f in findings)
    assert any("clipped-width" in f["kind"] for f in findings)
    assert any("outside-vertical" in f["kind"] for f in findings)


def test_detects_packed_but_unmapped_footer(window):
    tk.Frame(window,height=200).pack()
    tk.Button(window,text="Close").pack()
    window.update()
    assert any("unmapped" in f["kind"] for f in measure(window,("Close",))["findings"])


def test_scrollable_content_is_reachable_without_false_clipping(window):
    tk.Button(window,text="Close").pack(side="bottom")
    body=ScrollableBody(window); body.pack(fill="both",expand=True)
    for n in range(30): tk.Label(body.content,text=f"Row {n}").pack()
    window.update()
    assert not measure(window,("Close","Row 29"))["findings"]
    body.canvas.yview_moveto(1); window.update()
    last=body.content.winfo_children()[-1]
    assert last.winfo_rooty()+last.winfo_height() <= body.canvas.winfo_rooty()+body.canvas.winfo_height()+2


def test_notebook_checks_only_selected_page(window):
    book=ttk.Notebook(window);book.pack(fill="both",expand=True)
    for title in ("First","Second"):
        page=tk.Frame(book);book.add(page,text=title);tk.Label(page,text=title).pack()
    window.update()
    assert not measure(window)["findings"]
    book.select(1);window.update()
    assert not measure(window)["findings"]


def test_inventory_requires_a_scenario_or_documented_exclusion():
    from gui.tests.dialog_audit.inventory import construction_sites, EXCLUSIONS
    from gui.tests.dialog_audit.scenarios import CASES
    sites={site["id"] for site in construction_sites()}
    registered={case.site for case in CASES}
    assert sites <= registered | EXCLUSIONS.keys()
    assert not (EXCLUSIONS.keys() - sites), "Remove obsolete exclusions"
    assert len({case.name for case in CASES}) == len(CASES)


def test_fixture_paths_stay_temporary_and_external_operations_are_blocked(tmp_path):
    import socket
    import subprocess
    from shared.path_service import get_repo_root
    from shared import path_service
    from gui.tests.dialog_audit.environment import Environment
    with Environment(tmp_path) as env:
        assert path_service.get_paths(repo_root=get_repo_root()).home_root == env.paths.home_root
        with pytest.raises(RuntimeError, match="External operation blocked"):
            subprocess.run(["this-must-never-run"])
        with socket.socket() as connection:
            with pytest.raises(RuntimeError, match="External operation blocked"):
                connection.connect(("192.0.2.1", 80))


def test_top_level_wrap_ignores_child_configure_events(window):
    from gui.utils.scrollable_body import wrap_label
    label=tk.Label(window,text="A paragraph that must wrap across the dialog width.")
    label.pack(fill="x")
    wrap_label(label,window)
    button=tk.Button(window,text="OK");button.pack()
    window.update()
    original=int(label.cget("wraplength"))
    button.configure(width=2);window.update()
    assert int(label.cget("wraplength")) == original
    assert label.winfo_reqheight() < 100


def test_action_row_wraps_without_losing_buttons(window):
    from gui.utils.scrollable_body import flow_row
    row=tk.Frame(window);row.pack(fill="x")
    for text in ("Open report", "Export selected", "Resume", "Cancel"):
        tk.Button(row,text=text).pack(side="left")
    flow_row(row);window.update()
    assert len({w.winfo_y() for w in row.winfo_children()}) > 1
    assert not measure(window)["findings"]
    window.geometry("600x160");window.update()
    assert len({w.winfo_y() for w in row.winfo_children()}) == 1


def test_reachability_does_not_accept_an_incomplete_scroll_capture():
    from gui.tests.dialog_audit.inspect import unreachable
    def capture(interval):
        return {"title":"Example", "widgets":[{"path":"label", "text":"Long paragraph", "mapped":True,
                "actual":[100,300],"visible_y":interval}]}
    assert unreachable([capture([0,100]),capture([200,300])])
    assert not unreachable([capture([0,100]),capture([100,200]),capture([200,300])])


def test_detects_text_clipped_inside_tree_rows(window):
    style=ttk.Style(window)
    style.configure("Audit.Treeview", font=("Arial", 20), rowheight=10)
    tree=ttk.Treeview(window, style="Audit.Treeview")
    tree.pack(fill="both",expand=True)
    tree.insert("", "end", text="Readable row")
    window.update()
    assert any(f["kind"] == "clipped-tree-row" for f in measure(window)["findings"])
    line_height=int(window.tk.call("font", "metrics", ("Arial",20), "-linespace"))
    style.configure("Audit.Treeview", rowheight=line_height+4)
    window.update()
    assert not any(f["kind"] == "clipped-tree-row" for f in measure(window)["findings"])


def test_unpainted_screenshot_requires_review():
    from PIL import Image
    from gui.tests.dialog_audit.inspect import unpainted_controls
    evidence = {"widgets": [{"path":"button", "class":"Button", "text":"Close",
                "mapped":True, "rect":[0,0,100,30], "visible_y":[0,30]}]}
    assert unpainted_controls(Image.new("RGB", (100,30), "black"), evidence)
    assert not unpainted_controls(Image.new("RGB", (100,30), "white"), evidence)
