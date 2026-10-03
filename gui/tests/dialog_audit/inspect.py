"""Geometry evidence, independent of screenshots and expected-control assertions."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def text_of(widget):
    try:
        return str(widget.cget("text"))
    except tk.TclError:
        return ""


def inactive_page(widget, window):
    current = widget
    while current is not window:
        parent = current.master
        if isinstance(parent, ttk.Notebook) and str(current) != parent.select():
            return True
        current = parent
        if current is None:
            break
    return False


def rectangle(widget):
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    return [x, y, x + widget.winfo_width(), y + widget.winfo_height()]


def measure(window, expected=(), *, check_screen=False):
    """Report missing/obscured controls, allowing declared scroll viewports.

    Canvas window children may extend vertically outside the viewport only when
    a real scrollbar controls that canvas. Their own containers must still fit.
    Entries, trees, text and lists have intrinsically scrollable data; their
    requested size is not a minimum. Inactive notebook pages are checked later.
    """
    window.update_idletasks()
    rows, findings = [], []
    screen_width, screen_height = window.winfo_screenwidth(), window.winfo_screenheight()
    window_rect = rectangle(window)
    if check_screen and (window_rect[0] < 0 or window_rect[1] < 0 or window_rect[2] > screen_width or window_rect[3] > screen_height):
        findings.append({"kind": "window-offscreen", "rect": window_rect,
                         "screen": [screen_width, screen_height]})
    widgets = list(descendants(window))
    for required in expected:
        matches = [w for w in widgets if required in text_of(w)]
        if not matches:
            findings.append({"kind": "missing", "text": required})
    for widget in widgets:
        if inactive_page(widget, window) or isinstance(widget, (tk.Toplevel, tk.Menu)):
            continue
        cls = widget.winfo_class()
        text = text_of(widget)
        manager = widget.winfo_manager()
        # Unmanaged content is often an intentionally collapsed option panel.
        # Explicit expectations above/below handle controls required in a state.
        required = any(value in text for value in expected) if text else False
        if not manager and not required:
            continue
        if not text and cls not in {"Entry", "TEntry", "TCombobox", "Text", "Treeview", "Listbox", "Scale", "TScale"}:
            continue
        row = {"path": str(widget), "class": cls, "text": text,
               "mapped": bool(widget.winfo_ismapped()), "rect": rectangle(widget),
               "requested": [widget.winfo_reqwidth(), widget.winfo_reqheight()],
               "actual": [widget.winfo_width(), widget.winfo_height()]}
        rows.append(row)
        if isinstance(widget, ttk.Treeview):
            style = ttk.Style(widget)
            name = widget.cget("style") or "Treeview"
            font = style.lookup(name, "font") or "TkDefaultFont"
            line_height = int(widget.tk.call("font", "metrics", font, "-linespace"))
            row_height = int(style.lookup(name, "rowheight") or 20)
            row["row_height"] = row_height
            row["font_line_height"] = line_height
            if row_height < line_height:
                findings.append({"kind": "clipped-tree-row", "path": str(widget),
                                 "row_height": row_height, "font_line_height": line_height})
        # Retain visible vertical intervals for reachability checks across scroll
        # captures. A tall label is allowed to span several consecutive views.
        top, bottom = max(0, row["rect"][1]), min(screen_height, row["rect"][3])
        ancestor = widget.master
        while ancestor is not None:
            bounds = rectangle(ancestor)
            top, bottom = max(top, bounds[1]), min(bottom, bounds[3])
            if ancestor is window:
                break
            ancestor = ancestor.master
        row["visible_y"] = [max(0, top - row["rect"][1]), max(0, bottom - row["rect"][1])]
        problems = []
        if not row["mapped"]:
            # pack/grid children of explicitly hidden containers aren't expected.
            parent = widget.master
            hidden = False
            while parent is not window:
                if not parent.winfo_manager():
                    hidden = True
                    break
                parent = parent.master
            if required or not hidden:
                problems.append("unmapped")
        else:
            bounds = row["rect"]
            parent = widget.master
            allow_y = allow_x = False
            while parent is not None:
                if isinstance(parent, tk.Canvas):
                    allow_y = allow_y or bool(parent.cget("yscrollcommand"))
                    allow_x = allow_x or bool(parent.cget("xscrollcommand"))
                rect = rectangle(parent)
                if not allow_x and (bounds[0] < rect[0] - 2 or bounds[2] > rect[2] + 2):
                    problems.append("outside-horizontal-container")
                if not allow_y and (bounds[1] < rect[1] - 2 or bounds[3] > rect[3] + 2):
                    problems.append("outside-vertical-container")
                if parent is window:
                    break
                parent = parent.master
            if cls in {"Label", "TLabel", "Button", "TButton", "Checkbutton", "TCheckbutton", "Radiobutton", "TRadiobutton"}:
                if row["requested"][0] > row["actual"][0] + 2:
                    problems.append("clipped-width")
                if row["requested"][1] > row["actual"][1] + 2:
                    problems.append("clipped-height")
        if problems:
            findings.append({**row, "kind": ",".join(sorted(set(problems)))})
    return {"geometry": window.geometry(), "window_rect": window_rect,
            "title": window.title(), "widgets": rows, "findings": findings}


def unreachable(captures):
    """Find controls/content never exposed by any captured tab/scroll position."""
    items = {}
    for capture in captures:
        for widget in capture["widgets"]:
            if not widget["mapped"]:
                continue  # Already reported by measure where unexpected.
            key = (capture["title"], widget["path"], widget["text"])
            item = items.setdefault(key, {"height": 0, "intervals": []})
            item["height"] = max(item["height"], widget["actual"][1])
            item["intervals"].append(widget["visible_y"])
    findings = []
    for (title, path, text), item in items.items():
        end = 0
        for top, bottom in sorted(item["intervals"]):
            if bottom <= top:
                continue
            if top > end + 2:
                break
            end = max(end, bottom)
        if end < item["height"] - 2:
            findings.append({"kind": "unreachable-content", "title": title,
                             "path": path, "text": text, "visible_through": end,
                             "height": item["height"]})
    return findings


def unpainted_controls(screenshot, evidence):
    """A mapped text control captured as pure black needs visual review.

    X11 can report mapped geometry before its pixels have been painted. Do not
    silently approve such screenshots merely because their rectangles fit.
    """
    findings = []
    width, height = screenshot.size
    for row in evidence["widgets"]:
        if not row["mapped"] or not row["text"].strip() or row["class"] not in {
            "Label", "TLabel", "Button", "TButton", "Checkbutton", "TCheckbutton",
            "Radiobutton", "TRadiobutton",
        }:
            continue
        x1, y1, x2, y2 = row["rect"]
        top, bottom = row["visible_y"]
        bounds = (max(0, x1), max(0, y1 + top), min(width, x2), min(height, y1 + bottom))
        if bounds[2] - bounds[0] < 3 or bounds[3] - bounds[1] < 3:
            continue
        if not screenshot.crop(bounds).getbbox():
            findings.append({"kind": "review-unpainted-control", "path": row["path"],
                             "text": row["text"]})
    return findings
