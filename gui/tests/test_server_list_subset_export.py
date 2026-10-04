"""Card C2: real Tk menu/dialog wiring with isolated engines and messageboxes."""

import re
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gui.utils import safe_messagebox
from gui.utils.style import SMBSeekTheme


class MainThreadTk:
    """Fail immediately on any Tcl/Tk access from the export worker."""

    def __init__(self, interpreter):
        self.interpreter = interpreter
        self.violations = []

    def __getattr__(self, name):
        value = getattr(self.interpreter, name)
        if not callable(value):
            return value

        def checked(*args, **kwargs):
            if threading.current_thread() is not threading.main_thread():
                self.violations.append(name)
                raise AssertionError(f"Worker called Tk: {name}")
            return value(*args, **kwargs)

        return checked


@pytest.fixture
def ui(monkeypatch, tmp_path):
    # Patch every wrapper, including unexpected error paths, before importing UI.
    boxes = {}
    for name in safe_messagebox.__all__:
        if name.startswith(('show', 'ask')):
            boxes[name] = Mock(return_value=False)
            monkeypatch.setattr(safe_messagebox, name, boxes[name])
    from gui.components.server_list_window import export
    from gui.components.server_list_window.window import ServerListWindow

    root = tk.Tk()
    root.tk = MainThreadTk(root.tk)
    callback_errors = []
    root.report_callback_exception = lambda *error: callback_errors.append(error)
    root.geometry('900x400')
    theme = SMBSeekTheme()
    theme.setup_ttk_styles(root)
    source = tmp_path / 'active.db'
    source.touch()
    output = tmp_path / 'subset.db'
    chooser = Mock(return_value=str(output))
    monkeypatch.setattr(export.filedialog, 'asksaveasfilename', chooser)
    calls = []
    done = threading.Event()
    release = threading.Event()
    result = dict(success=True, cancelled=False, error=None, hosts={'S': 1, 'F': 1, 'H': 1},
                  rows={'smb_servers': 1, 'ftp_servers': 1, 'http_servers': 1, 'share_access': 4},
                  size_bytes=2 * 1024 * 1024, output_path=str(output), missing=['H:99'],
                  warnings=['Unknown source table excluded'])
    engine = SimpleNamespace(run=lambda kwargs: result)

    class FakeEngine:
        def __init__(self, path):
            assert path == str(source)

        def export_subset(self, filename, keys, **kwargs):
            assert threading.current_thread() is not threading.main_thread()
            assert threading.current_thread().daemon
            calls.append((filename, keys, kwargs))
            try:
                return engine.run(kwargs)
            finally:
                done.set()

    monkeypatch.setattr(export, 'DBToolsEngine', FakeEngine)
    focus_calls = []
    real_focus = export.ensure_dialog_focus

    def focus(dialog, parent):
        assert dialog.grab_current() == dialog
        assert dialog.winfo_children()
        focus_calls.append(dialog)
        real_focus(dialog, parent)

    monkeypatch.setattr(export, 'ensure_dialog_focus', focus)
    rows = [{'row_key': key} for key in ('S:1', 'F:2', 'H:3')]
    window = ServerListWindow.__new__(ServerListWindow)
    window.window = root
    window.theme = theme
    window.db_reader = SimpleNamespace(db_path=source)
    window.filtered_servers = rows
    window.active_jobs = {}
    window.tree = ttk.Treeview(root)
    for row in rows:
        window.tree.insert('', 'end', iid=row['row_key'])
    window.tree.selection_set('S:1', 'H:3')
    window.table_frame = tk.Frame(root)
    window.table_frame.pack()
    window._initialize_running_tasks_button = lambda: None
    window._update_action_buttons_state = lambda: None
    window._create_button_panel()
    root.update()
    yield SimpleNamespace(**locals())
    release.set()
    if calls:
        assert done.wait(2), 'Fake export worker did not finish'
    root.destroy()
    assert not callback_errors
    assert not root.tk.violations


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(dialog, label):
    return next(w for w in descendants(dialog) if isinstance(w, tk.Button) and w['text'] == label)


def dialog(ui, title):
    return next(w for w in ui.root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == title)


def close_window(widget):
    widget.tk.call(widget.protocol('WM_DELETE_WINDOW'))


def pump(ui, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        ui.root.update()
        time.sleep(0.005)
    assert predicate(), 'UI polling did not deliver the expected update'


def dropdown(ui):
    ui.window.export_button.invoke()
    return [w for w in ui.root.winfo_children() if isinstance(w, tk.Menu)][-1]


def confirm(ui):
    ui.window._save_selected_to_database()
    return dialog(ui, 'Save Hosts to Database')


def start(ui):
    button(confirm(ui), 'Save').invoke()
    return dialog(ui, 'Saving Hosts to Database')


def test_context_menu_selection_group_and_callback(ui, monkeypatch):
    ui.window._create_context_menu(ui.window.tree)
    menu = ui.window.context_menu
    index = menu.index('📤 Export Selected…')
    assert menu.entrycget(index - 2, 'label') == '🔗 Copy URL'
    assert menu.type(index - 1) == menu.type(index + 1) == 'separator'
    assert menu.entrycget(index, 'state') == 'normal'
    save = Mock()
    monkeypatch.setattr(ui.export, 'save_hosts_to_database', save)
    menu.invoke(index)
    save.assert_called_once_with(ui.root, [ui.rows[0], ui.rows[2]], ui.theme, ui.source)
    ui.window.tree.selection_remove(*ui.window.tree.selection())
    ui.window._update_context_menu_state()
    assert menu.entrycget(index, 'state') == 'disabled'


@pytest.mark.parametrize('selected,shown', [(True, True), (False, True), (False, False)])
def test_dropdown_labels_states_and_position(ui, monkeypatch, selected, shown):
    if not selected:
        ui.window.tree.selection_remove(*ui.window.tree.selection())
    if not shown:
        ui.window.filtered_servers = []
    posted = Mock()
    monkeypatch.setattr(tk.Menu, 'post', posted)
    menu = dropdown(ui)
    labels = [menu.entrycget(i, 'label') for i in range(9) if i != 4]
    assert labels == [f'{scope} → {fmt}' for scope in ('Selected', 'All shown')
                      for fmt in ('Database…', 'CSV', 'JSON', 'ZIP')]
    assert menu.type(4) == 'separator'
    for indices, enabled in ((range(4), selected), (range(5, 9), shown)):
        assert all(menu.entrycget(i, 'state') == ('normal' if enabled else 'disabled') for i in indices)
    export_button = ui.window.export_button
    posted.assert_called_once_with(export_button.winfo_rootx(),
                                   export_button.winfo_rooty() + export_button.winfo_height())
    siblings = export_button.master.winfo_children()
    assert siblings.index(export_button) + 1 == siblings.index(ui.window.delete_button)
    assert export_button['background'] == ui.theme.styles['button_secondary']['bg']


@pytest.mark.parametrize('scope,index', [('selected', 1), ('all', 6)])
@pytest.mark.parametrize('fmt,offset', [('csv', 0), ('json', 1), ('zip', 2)])
def test_existing_formats_receive_correct_data(ui, monkeypatch, scope, index, fmt, offset):
    export_format = Mock()
    monkeypatch.setattr(ui.export, 'export_servers_to_format', export_format)
    menu = dropdown(ui)
    menu.invoke(index + offset)
    args = export_format.call_args.args
    assert args[:3] == (ui.rows if scope == 'all' else [ui.rows[0], ui.rows[2]], scope, fmt)
    assert args[3:5] == (ui.root, ui.theme)


def test_save_as_cancel(ui):
    ui.chooser.return_value = ''
    ui.window._save_selected_to_database()
    assert not ui.calls and not ui.focus_calls
    options = ui.chooser.call_args.kwargs
    assert options['parent'] == ui.root
    assert options['title'] == 'Save Hosts to Database'
    assert options['defaultextension'] == '.db'
    assert options['filetypes'] == [('SQLite databases', '*.db'), ('All files', '*.*')]
    assert re.fullmatch(r'dirracuda_subset_\d{8}_\d{6}\.db', options['initialfile'])


@pytest.mark.parametrize('alias', [False, True])
def test_active_database_guard(ui, alias):
    path = ui.source
    if alias:
        path = ui.tmp_path / 'alias.db'
        path.symlink_to(ui.source)
    ui.chooser.return_value = str(path)
    ui.window._save_selected_to_database()
    assert not ui.calls and not ui.focus_calls
    ui.boxes['showerror'].assert_called_once()
    assert 'active database cannot be overwritten' in ui.boxes['showerror'].call_args.args[1]


@pytest.mark.parametrize('close', [False, True])
def test_confirm_cancel_or_window_close(ui, close):
    confirmation = confirm(ui)
    if close:
        close_window(confirmation)
    else:
        button(confirmation, 'Cancel').invoke()
    assert not confirmation.winfo_exists()
    assert not ui.calls
    assert not any(box.called for box in ui.boxes.values())


@pytest.mark.parametrize('credentials', [True, False])
def test_confirm_save_selection_credentials_and_success(ui, credentials):
    confirmation = confirm(ui)
    labels = [w['text'] for w in descendants(confirmation) if isinstance(w, tk.Label)]
    assert f'Save 2 hosts (SMB 1 · FTP 0 · HTTP 1) to {ui.output.name}?' in labels
    assert 'Anyone with this file can read these credentials.' in labels
    assert 'Analyst results are not included.' in labels
    checkbox = next(w for w in descendants(confirmation) if isinstance(w, tk.Checkbutton))
    assert ui.root.getboolean(checkbox.getvar(checkbox['variable']))
    if not credentials:
        checkbox.invoke()
    button(confirmation, 'Save').invoke()
    progress = dialog(ui, 'Saving Hosts to Database')
    assert ui.done.wait(2)
    # Worker finished, but no UI result/teardown occurs until after() polls.
    assert progress.winfo_exists() and not ui.boxes['showinfo'].called
    pump(ui, lambda: ui.boxes['showinfo'].called)
    filename, keys, kwargs = ui.calls[0]
    assert filename == str(ui.output) and keys == ['S:1', 'H:3']
    assert kwargs['include_credentials'] is credentials
    assert len(ui.focus_calls) == 2
    assert not progress.winfo_exists()
    title, text = ui.boxes['showinfo'].call_args.args
    assert title == 'Export Complete'
    for fragment in ('Saved 3 hosts (SMB 1 · FTP 1 · HTTP 1)', 'Rows: 7', 'Size: 2.00 MB',
                     str(ui.output), 'Missing hosts: 1', 'Unknown source table excluded'):
        assert fragment in text
    assert ui.boxes['showinfo'].call_args.kwargs['parent'] == ui.root


@pytest.mark.parametrize('all_shown,index', [(False, 0), (True, 5)])
def test_database_dropdown_passes_row_keys(ui, all_shown, index):
    menu = dropdown(ui)
    menu.invoke(index)
    menu.unpost()
    button(dialog(ui, 'Save Hosts to Database'), 'Save').invoke()
    pump(ui, lambda: ui.boxes['showinfo'].called)
    assert ui.calls[0][1] == (['S:1', 'F:2', 'H:3'] if all_shown else ['S:1', 'H:3'])


@pytest.mark.parametrize('raises', [False, True])
def test_failure_result_and_engine_exception(ui, raises):
    def fail(kwargs):
        if raises:
            raise ValueError('Engine failure detail')
        return dict(success=False, cancelled=False, error='Engine failure detail')

    ui.engine.run = fail
    progress = start(ui)
    pump(ui, lambda: ui.boxes['showerror'].called)
    ui.boxes['showerror'].assert_called_once_with('Export Error', 'Engine failure detail', parent=ui.root)
    assert not ui.boxes['showinfo'].called
    assert not progress.winfo_exists()


@pytest.mark.parametrize('close', [False, True])
def test_progress_cancel_waits_for_worker_and_tears_down_on_ui_thread(ui, close):
    def wait_for_cancel(kwargs):
        kwargs['progress_callback'](45, 'Copying hosts')
        assert kwargs['cancel_event'].wait(2)
        assert ui.release.wait(2)
        return dict(success=False, cancelled=True, error='Export cancelled')

    ui.engine.run = wait_for_cancel
    progress = start(ui)
    destroyed_on = []
    progress.bind('<Destroy>', lambda event: destroyed_on.append(threading.current_thread())
                  if event.widget == progress else None, add='+')
    label = next(w for w in progress.winfo_children() if isinstance(w, tk.Label))
    pump(ui, lambda: label['text'] == 'Copying hosts')
    bar = next(w for w in progress.winfo_children() if isinstance(w, ttk.Progressbar))
    assert bar['value'] == 45
    if close:
        close_window(progress)
    else:
        button(progress, 'Cancel').invoke()
    assert ui.calls[0][2]['cancel_event'].is_set()
    assert progress.winfo_exists() and not ui.boxes['showinfo'].called
    ui.release.set()
    pump(ui, lambda: ui.boxes['showinfo'].called)
    assert destroyed_on == [threading.main_thread()]
    assert not progress.winfo_exists()
    ui.boxes['showinfo'].assert_called_once_with('Export Cancelled', 'Export cancelled.', parent=ui.root)


def test_dialog_destroy_cancels_worker_and_pending_poll(ui):
    def wait_for_cancel(kwargs):
        assert kwargs['cancel_event'].wait(2)
        return dict(success=False, cancelled=True, error='Export cancelled')

    ui.engine.run = wait_for_cancel
    progress = start(ui)
    progress.destroy()
    assert ui.done.wait(2)
    assert ui.calls[0][2]['cancel_event'].is_set()
    assert not ui.root.tk.call('after', 'info')
    assert not any(box.called for box in ui.boxes.values())
