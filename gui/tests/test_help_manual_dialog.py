"""Unit tests for in-app User Manual helpers and dialog wiring."""

from __future__ import annotations

from pathlib import Path

from gui.components import help_manual_dialog as hmd


class _BindStub:
    def __init__(self) -> None:
        self.bindings = {}

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback


class _TextStub:
    def __init__(self) -> None:
        self.inserted = []

    def insert(self, _where, text, tags=None):
        self.inserted.append((text, tags))

    def image_create(self, _where, image=None):
        self.inserted.append(("<image>", image))


class _WindowStub:
    def __init__(self) -> None:
        self.exists = True
        self.lifted = 0
        self.focused = 0
        self.bound = {}

    def winfo_exists(self):
        return self.exists

    def lift(self):
        self.lifted += 1

    def focus_force(self):
        self.focused += 1

    def bind(self, sequence, callback, add=None):
        self.bound[(sequence, add)] = callback


class _OwnerStub:
    def __init__(self) -> None:
        self._help_manual_dialog = None

    def winfo_toplevel(self):
        return self


class _NavTreeStub:
    def __init__(self) -> None:
        self._selection = ()
        self._exists = set()
        self.selection_set_calls = 0

    def selection(self):
        return self._selection

    def selection_set(self, item_id):
        self._selection = (item_id,)
        self.selection_set_calls += 1

    def exists(self, item_id):
        return item_id in self._exists


def test_slugify_anchor_normalizes_and_stabilizes() -> None:
    assert hmd.slugify_anchor("Keyboard Shortcuts (Phase 1 + Phase 2)") == "keyboard-shortcuts-phase-1-phase-2"
    assert hmd.slugify_anchor("  ### Weird---Header ### ") == "weird-header"


def test_extract_nav_sections_returns_h1_h2_only_with_unique_anchors() -> None:
    markdown = "# Title\n## A\n## A\n### Deep\n"
    sections = hmd.extract_nav_sections(markdown, max_level=2)
    assert sections == [
        (1, "Title", "title"),
        (2, "A", "a"),
        (2, "A", "a-1"),
    ]


def test_parse_markdown_link_target_handles_external_anchor_and_doc_paths(tmp_path: Path) -> None:
    current = tmp_path / "README.md"

    external = hmd.parse_markdown_link_target("https://example.com/a", current_doc_path=current)
    assert external.kind == "external"
    assert external.url == "https://example.com/a"

    anchor = hmd.parse_markdown_link_target("#Keyboard-Shortcuts", current_doc_path=current)
    assert anchor.kind == "anchor"
    assert anchor.anchor == "keyboard-shortcuts"

    doc_anchor = hmd.parse_markdown_link_target("docs/TECHNICAL_REFERENCE.md#Keyboard Contract", current_doc_path=current)
    assert doc_anchor.kind == "doc"
    assert doc_anchor.target_path == (tmp_path / "docs" / "TECHNICAL_REFERENCE.md").resolve(strict=False)
    assert doc_anchor.anchor == "keyboard-contract"


def test_is_markdown_table_separator_detection() -> None:
    assert hmd.is_markdown_table_separator("|---|:---:|---:|") is True
    assert hmd.is_markdown_table_separator("| name | value |") is False


def test_compute_scaled_dimensions_preserves_aspect() -> None:
    assert hmd.compute_scaled_dimensions(1200, 600, 600) == (600, 300)
    assert hmd.compute_scaled_dimensions(500, 250, 600) == (500, 250)


def test_load_markdown_document_missing_file_returns_placeholder(tmp_path: Path) -> None:
    missing = tmp_path / "missing.md"
    content, exists = hmd.load_markdown_document(missing, title="Missing Doc")
    assert exists is False
    assert "Document is currently unavailable" in content
    assert str(missing) in content


def test_open_help_manual_dialog_reuses_existing_window(monkeypatch) -> None:
    owner = _OwnerStub()
    created = []

    class _DialogStub:
        def __init__(self, _owner, *, theme=None):
            _ = theme
            created.append(True)
            self.window = _WindowStub()

    monkeypatch.setattr(hmd, "UserManualDialog", _DialogStub)

    first = hmd.open_help_manual_dialog(owner, theme=None)
    second = hmd.open_help_manual_dialog(owner, theme=None)

    assert first is second
    assert len(created) == 1
    assert second.lifted == 1
    assert second.focused == 1


def test_bind_keyboard_shortcuts_registers_close_contract() -> None:
    dlg = hmd.UserManualDialog.__new__(hmd.UserManualDialog)
    calls = {"close": 0}
    dlg.window = _BindStub()
    dlg._close = lambda: calls.__setitem__("close", calls["close"] + 1)

    hmd.UserManualDialog._bind_keyboard_shortcuts(dlg)

    assert dlg.window.bindings["<Escape>"](None) == "break"
    assert dlg.window.bindings["<Control-w>"](None) == "break"
    assert calls["close"] == 2


def test_insert_image_line_handles_missing_image_without_crash(tmp_path: Path) -> None:
    dlg = hmd.UserManualDialog.__new__(hmd.UserManualDialog)
    dlg.text_widget = _TextStub()
    dlg._image_refs = []

    source = tmp_path / "README.md"
    hmd.UserManualDialog._insert_image_line(dlg, "img/not_found.png", "missing", source_doc_path=source)

    assert any("missing image" in text for text, _tags in dlg.text_widget.inserted)


def test_show_document_skips_programmatic_reselect_when_from_nav() -> None:
    dlg = hmd.UserManualDialog.__new__(hmd.UserManualDialog)
    doc = hmd.ManualDocument(
        key="readme",
        title="README",
        path=Path("/tmp/README.md"),
        content="# README\n",
        sections=[],
        exists=True,
    )
    dlg._doc_by_key = {"readme": doc}
    dlg._current_doc_key = None
    dlg.nav_tree = _NavTreeStub()
    dlg.nav_tree._exists.add("doc:readme")
    dlg.text_widget = None
    dlg._render_markdown = lambda _doc: None
    dlg._jump_to_anchor = lambda _anchor: None
    dlg._set_status = lambda _text: None

    hmd.UserManualDialog._show_document(dlg, "readme", from_nav=True)
    assert dlg.nav_tree.selection_set_calls == 0


def test_show_document_programmatic_select_only_when_needed() -> None:
    dlg = hmd.UserManualDialog.__new__(hmd.UserManualDialog)
    doc = hmd.ManualDocument(
        key="readme",
        title="README",
        path=Path("/tmp/README.md"),
        content="# README\n",
        sections=[],
        exists=True,
    )
    dlg._doc_by_key = {"readme": doc}
    dlg._current_doc_key = None
    dlg.nav_tree = _NavTreeStub()
    dlg.nav_tree._exists.add("doc:readme")
    dlg._suspend_nav_select = False
    dlg.text_widget = None
    dlg._render_markdown = lambda _doc: None
    dlg._jump_to_anchor = lambda _anchor: None
    dlg._set_status = lambda _text: None

    hmd.UserManualDialog._show_document(dlg, "readme", from_nav=False)
    assert dlg.nav_tree.selection_set_calls == 1

    hmd.UserManualDialog._show_document(dlg, "readme", from_nav=False)
    assert dlg.nav_tree.selection_set_calls == 1
