"""One isolated scenario process, including Tk's real modal wait lifecycle."""
import json
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def run(case, output, scale, dark):
    import tkinter as tk
    from tkinter import ttk
    from PIL import ImageGrab
    from .environment import Environment
    from .inspect import descendants, measure, unreachable, unpainted_controls
    from .scenarios import build
    output.mkdir(parents=True, exist_ok=True)
    result = {"scenario":case.name, "site":case.site, "captures":[], "errors":[]}
    with TemporaryDirectory(prefix="dirracuda-audit-") as directory, Environment(Path(directory)) as env:
        root=tk.Tk(); env.root=root
        root.tk.call("tk", "scaling", scale)
        root.geometry(f"800x250+{(root.winfo_screenwidth()-800)//2}+{(root.winfo_screenheight()-250)//2}")
        root.title("Dialog audit fixture")
        from gui.utils import style
        style.theme=style.SMBSeekTheme(use_dark_mode=dark)
        style.theme.setup_ttk_styles(root)
        def callback_error(exc, value, tb):
            result["errors"].append("".join(traceback.format_exception(exc,value,tb)))
        root.report_callback_exception=callback_error
        captured=set()
        def settle():
            deadline=time.monotonic()+0.12
            while time.monotonic()<deadline:
                root.update(); time.sleep(.01)
        def snapshot(window, state):
            settle()
            evidence=measure(window, case.expected, check_screen=True)
            evidence["state"]=state
            # Capture the full desktop to retain evidence of offscreen placement.
            filename=f"{len(result['captures']):03d}.png"
            screenshot = ImageGrab.grab(xdisplay=root.winfo_screen())
            screenshot.save(output/filename)
            evidence["findings"].extend(unpainted_controls(screenshot, evidence))
            evidence["screenshot"]=filename
            result["captures"].append(evidence)
        def capture(window):
            key=str(window)
            if key in captured or not window.winfo_exists(): return
            captured.add(key)
            window.lift(); settle()
            snapshot(window,"default")
            # Every notebook page is selected explicitly, including nested pages.
            notebooks=[w for w in descendants(window) if isinstance(w,ttk.Notebook)]
            for book in notebooks:
                for tab in book.tabs():
                    if book.tab(tab,"state")=="hidden": continue
                    book.select(tab); settle()
                    snapshot(window,"tab:"+str(book.tab(tab,"text")))
                    scroll(window)
            scroll(window)
        def scroll(window):
            for canvas in [w for w in descendants(window) if isinstance(w,tk.Canvas) and w.winfo_ismapped()]:
                if not canvas.cget("yscrollcommand"): continue
                bounds=canvas.bbox("all")
                if not bounds or bounds[3]-bounds[1]<=canvas.winfo_height()+2: continue
                # Entire tall labels may span several viewports; visit all pages.
                step=max(.01,canvas.winfo_height()/max(1,bounds[3]-bounds[1]))
                fraction=step
                while fraction<1+step:
                    canvas.yview_moveto(min(1,fraction)); settle()
                    snapshot(window,f"scroll:{canvas}:{min(1,fraction):.3f}")
                    fraction+=step
                canvas.yview_moveto(0)
            # Text and tabular data scroll inside their own widgets. Capture
            # their end positions as well, without treating rows as controls.
            for widget in descendants(window):
                if not isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)) or not widget.winfo_ismapped():
                    continue
                for axis in ("y", "x"):
                    view = getattr(widget, axis + "view")
                    start, end = view()
                    if end - start >= .999:
                        continue
                    getattr(widget, axis + "view_moveto")(1)
                    snapshot(window, f"data-scroll:{widget}:{axis}:end")
                    getattr(widget, axis + "view_moveto")(start)
        real_wait=tk.Misc.wait_window
        def modal_wait(owner, window=None):
            target=window if window is not None else owner
            def inspect_and_close():
                try: capture(target)
                except Exception: result["errors"].append(traceback.format_exc())
                finally:
                    if target.winfo_exists(): target.destroy()
            root.after(100,inspect_and_close)
            return real_wait(owner,target)
        try:
            settle()
            with patch.object(tk.Misc,"wait_window",modal_wait):
                env.capture=capture
                obj=build(case,env)
                settle()
                windows=[w for w in descendants(root) if isinstance(w,tk.Toplevel)]
                # Nested forms are the scenario; their parent has its own case.
                if windows and not result["captures"]:
                    capture(windows[-1])
            if not result["captures"]: result["errors"].append("Scenario produced no window")
            result["blocked_operations"]=env.attempts
        except Exception:
            result["errors"].append(traceback.format_exc())
        finally:
            root.destroy()
    result["reachability_findings"] = unreachable(result["captures"])
    result["passed"]=not result["reachability_findings"] and not result["errors"] and not any(c["findings"] for c in result["captures"])
    (output/"result.json").write_text(json.dumps(result,indent=2))
    return result
