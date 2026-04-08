"""Unit tests for gui.utils.safe_messagebox."""

from unittest.mock import MagicMock, patch

from gui.utils import safe_messagebox


def _make_widget() -> MagicMock:
    widget = MagicMock()
    widget.winfo_exists.return_value = 1
    widget.winfo_toplevel.return_value = widget
    return widget


def test_showinfo_with_explicit_parent_uses_focus_hooks() -> None:
    parent = _make_widget()
    with patch.object(safe_messagebox, "_prepare_parent") as prep, patch.object(
        safe_messagebox, "_restore_parent"
    ) as restore, patch.object(
        safe_messagebox._tk_messagebox, "showinfo", return_value="ok"
    ) as showinfo:
        result = safe_messagebox.showinfo("Title", "Body", parent=parent)

    assert result == "ok"
    assert showinfo.call_args.kwargs["parent"] is parent
    prep.assert_called_once_with(parent)
    restore.assert_called_once_with(parent)


def test_askyesno_without_parent_falls_back_to_focused_toplevel() -> None:
    parent = _make_widget()
    focused_widget = _make_widget()
    focused_widget.winfo_toplevel.return_value = parent

    root = _make_widget()
    root.grab_current.return_value = None
    root.focus_get.return_value = focused_widget

    with patch.object(safe_messagebox.tk, "_default_root", root, create=True), patch.object(
        safe_messagebox._tk_messagebox, "askyesno", return_value=True
    ) as askyesno:
        result = safe_messagebox.askyesno("Confirm", "Proceed?")

    assert result is True
    assert askyesno.call_args.kwargs["parent"] is parent


def test_showerror_without_parent_or_root_does_not_inject_parent() -> None:
    with patch.object(safe_messagebox.tk, "_default_root", None, create=True), patch.object(
        safe_messagebox._tk_messagebox, "showerror", return_value="ok"
    ) as showerror:
        result = safe_messagebox.showerror("Error", "Boom")

    assert result == "ok"
    assert "parent" not in showerror.call_args.kwargs
