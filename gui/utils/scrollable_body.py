"""A vertically scrollable form body; put persistent actions outside this frame."""

import tkinter as tk
from tkinter import ttk

from gui.utils.style import get_theme


class ScrollableBody(tk.Frame):
    """Keep content at viewport width, showing a scrollbar only for overflow.

    Wheel bindings belong to this window, not bind_all, and are removed when
    the body is destroyed. Several bodies in notebook pages can coexist.
    """

    def __init__(self, parent):
        super().__init__(parent)
        theme = get_theme()
        theme.apply_to_widget(self, "main_window")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=self.cget("background"))
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = tk.Frame(self.canvas)
        theme.apply_to_widget(self.content, "main_window")
        self._item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._layout)
        self.canvas.bind("<Configure>", self._layout)
        self._owner = self.winfo_toplevel()
        self._wheel_bindings = {
            event: self._owner.bind(event, self._wheel, add="+")
            for event in ("<MouseWheel>", "<Button-4>", "<Button-5>")
        }
        self.bind("<Destroy>", self._destroyed, add="+")

    def _layout(self, _event=None):
        wrap_form_labels(self.content)
        self.canvas.itemconfigure(self._item, width=max(1, self.canvas.winfo_width()))
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.content.winfo_reqheight() > self.canvas.winfo_height() + 2:
            self.scrollbar.grid(row=0, column=1, sticky="ns")
        else:
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def _wheel(self, event):
        widget = event.widget
        if widget.winfo_class() in {"Text", "Listbox", "Treeview", "TCombobox", "Spinbox", "TSpinbox"}:
            return None  # Let data widgets consume their own wheel events.
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is not self or not self.scrollbar.winfo_ismapped():
            return None
        delta = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def _destroyed(self, event):
        if event.widget is self:
            for sequence, binding in self._wheel_bindings.items():
                self._owner.unbind(sequence, binding)


def wrap_label(label, container, *, padding=0):
    """Wrap a label to its allocated container width, including on font changes."""
    def resize(event):
        if event.widget is not container:
            return  # Toplevel bind tags also receive descendant Configure events.
        inset = sum(int(label.cget(option)) for option in
                    ("padx", "borderwidth", "highlightthickness"))
        width = max(1, event.width - padding - 2 * inset)
        if int(label.cget("wraplength")) != width:
            label.configure(wraplength=width)
    container.bind("<Configure>", resize, add="+")


def wrap_form_labels(container):
    """Wrap full-width prose labels in a form, preserving inline field labels."""
    for widget in container.winfo_children():
        wrap_form_labels(widget)
        if widget.winfo_class() not in {"Label", "TLabel"} or widget.winfo_manager() != "pack":
            continue
        info = widget.pack_info()
        if info.get("side") not in {"top", "bottom"} or getattr(widget, "_form_wrap_bound", False):
            continue
        widget._form_wrap_bound = True
        pads = info.get("padx", 0)
        if isinstance(pads, (tuple, list)):
            padding = sum(int(p) for p in pads)
        else:
            padding = int(pads) * 2
        try:
            inside = container.cget("padding")
            values = container.tk.splitlist(inside)
            pixels = [container.winfo_pixels(value) for value in values]
            if pixels:
                padding += pixels[0] + (pixels[2] if len(pixels) == 4 else pixels[0])
        except tk.TclError:
            pass  # Classic Tk frames do not expose ttk's padding option.
        # ttk labels have different padding options; use a separate callback.
        def resize(event, label=widget, inset=padding):
            if event.widget is not container:
                return
            label.configure(wraplength=max(1, event.width - inset - 12))
        container.bind("<Configure>", resize, add="+")
        if container.winfo_width() > 1:
            resize(type("Size", (), {"width": container.winfo_width(), "widget": container})())


def flow_row(frame, *, gap=6):
    """Lay out a small action row in additional lines when fonts need room.

    Call after adding the row's controls. Unlike shrinking buttons, this keeps
    each label legible and each action available at the current window width.
    """
    children = list(frame.winfo_children())
    for child in children:
        child.pack_forget()
        child.grid_forget()
    frame.pack_propagate(False)
    def layout(_event=None):
        width = max(1, frame.winfo_width())
        x = y = row_height = 0
        for child in children:
            w, h = child.winfo_reqwidth(), child.winfo_reqheight()
            if x and x + w > width:
                x = 0
                y += row_height + gap
                row_height = 0
            child.place(x=x, y=y, width=min(w, width), height=h)
            x += w + gap
            row_height = max(row_height, h)
        height = y + row_height
        if frame.winfo_reqheight() != height:
            frame.configure(height=height)
    frame.bind("<Configure>", layout, add="+")
    frame.after_idle(layout)


def fit_tree_headings(tree):
    """Keep column headings legible when the active font grows."""
    style = ttk.Style(tree)
    font = style.lookup("Treeview.Heading", "font") or "TkHeadingFont"
    columns = list(tree.cget("columns"))
    if "tree" in tree.cget("show"):
        columns.insert(0, "#0")
    for column in columns:
        heading = tree.heading(column, "text")
        minimum = int(tree.tk.call("font", "measure", font, heading)) + 16
        tree.column(column, minwidth=minimum,
                    width=max(int(tree.column(column, "width")), minimum))
