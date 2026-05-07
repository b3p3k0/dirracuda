"""In-app User Manual dialog with two-pane navigation and markdown rendering."""

from __future__ import annotations

import re
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - runtime dependency
    Image = None
    ImageTk = None

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:  # pragma: no cover - legacy import path
    from utils.dialog_helpers import ensure_dialog_focus  # type: ignore[no-redef]

from gui.utils.keybindings import add_shortcut_hint, bind_close_shortcuts


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")


@dataclass(frozen=True)
class ManualDocSpec:
    key: str
    title: str
    rel_path: str


@dataclass(frozen=True)
class ManualDocument:
    key: str
    title: str
    path: Path
    content: str
    sections: List[Tuple[int, str, str]]
    exists: bool


@dataclass(frozen=True)
class ParsedLinkTarget:
    kind: str  # external | anchor | doc
    target_path: Optional[Path] = None
    anchor: Optional[str] = None
    url: Optional[str] = None


DOC_SPECS: Tuple[ManualDocSpec, ...] = (
    ManualDocSpec("readme", "README", "README.md"),
    ManualDocSpec("tech_ref", "Technical Reference", "docs/TECHNICAL_REFERENCE.md"),
    ManualDocSpec("kbd", "Keyboard Quick Reference", "docs/KBD_QUICKREF.md"),
)


def manual_repo_root() -> Path:
    """Return canonical repository root path for manual source resolution."""
    return Path(__file__).resolve().parents[2]


def slugify_anchor(text: str) -> str:
    """Create a stable markdown-like heading anchor."""
    value = re.sub(r"[`*_~\[\](){}<>]", "", str(text or "").strip().lower())
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "section"


def _unique_anchor(base: str, seen: Dict[str, int]) -> str:
    index = seen.get(base, 0)
    seen[base] = index + 1
    if index == 0:
        return base
    return f"{base}-{index}"


def extract_nav_sections(markdown_text: str, *, max_level: int = 2) -> List[Tuple[int, str, str]]:
    """Extract heading sections for left navigation (H1/H2 by default)."""
    sections: List[Tuple[int, str, str]] = []
    seen: Dict[str, int] = {}
    for raw_line in (markdown_text or "").splitlines():
        match = _HEADING_RE.match(raw_line)
        if not match:
            continue
        level = len(match.group(1))
        if level > max_level:
            continue
        title = match.group(2).strip()
        anchor = _unique_anchor(slugify_anchor(title), seen)
        sections.append((level, title, anchor))
    return sections


def extract_all_heading_anchors(markdown_text: str) -> List[Tuple[int, str, str]]:
    """Extract all heading levels with unique anchors for in-document jumping."""
    headings: List[Tuple[int, str, str]] = []
    seen: Dict[str, int] = {}
    for raw_line in (markdown_text or "").splitlines():
        match = _HEADING_RE.match(raw_line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        anchor = _unique_anchor(slugify_anchor(title), seen)
        headings.append((level, title, anchor))
    return headings


def is_markdown_table_separator(line: str) -> bool:
    """Return True when the line is a markdown table separator row."""
    text = str(line or "").strip()
    if "|" not in text:
        return False
    parts = [part.strip() for part in text.strip("|").split("|")]
    if not parts:
        return False
    for part in parts:
        if not part:
            return False
        token = part.replace(":", "").replace("-", "")
        if token:
            return False
        if "-" not in part:
            return False
    return True


def compute_scaled_dimensions(width: int, height: int, max_width: int) -> Tuple[int, int]:
    """Scale dimensions to max_width while preserving aspect ratio."""
    w = int(width)
    h = int(height)
    mw = int(max_width)
    if w <= 0 or h <= 0:
        return (1, 1)
    if mw <= 0:
        return (w, h)
    if w <= mw:
        return (w, h)
    scale = float(mw) / float(w)
    return (mw, max(1, int(h * scale)))


def parse_markdown_link_target(target: str, *, current_doc_path: Path) -> ParsedLinkTarget:
    """Parse markdown link target into external/anchor/doc categories."""
    raw = str(target or "").strip()
    if not raw:
        return ParsedLinkTarget(kind="anchor", anchor="section")

    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        return ParsedLinkTarget(kind="external", url=raw)

    if raw.startswith("#"):
        return ParsedLinkTarget(kind="anchor", anchor=slugify_anchor(raw.lstrip("#")))

    path_part, sep, anchor_part = raw.partition("#")
    if path_part:
        if Path(path_part).is_absolute():
            resolved = Path(path_part).resolve(strict=False)
        else:
            resolved = (current_doc_path.parent / path_part).resolve(strict=False)
    else:
        resolved = current_doc_path.resolve(strict=False)

    anchor = slugify_anchor(anchor_part) if sep else None
    return ParsedLinkTarget(kind="doc", target_path=resolved, anchor=anchor)


def load_markdown_document(path: Path, *, title: str) -> Tuple[str, bool]:
    """Read markdown from disk, returning placeholder content when missing."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
        return text, True
    except Exception:
        placeholder = (
            f"# {title}\n\n"
            "Document is currently unavailable.\n\n"
            f"Expected path: `{file_path}`\n"
        )
        return placeholder, False


def resolve_image_path(image_target: str, *, source_doc_path: Path) -> Path:
    """Resolve markdown image target relative to the source markdown document."""
    target = str(image_target or "").strip()
    if not target:
        return source_doc_path
    if Path(target).is_absolute():
        return Path(target).resolve(strict=False)
    return (source_doc_path.parent / target).resolve(strict=False)


class UserManualDialog:
    """Two-pane in-app manual window."""

    def __init__(self, owner: tk.Misc, *, theme: Any = None) -> None:
        self.owner = owner
        self.theme = theme
        self.repo_root = manual_repo_root()

        self.documents: List[ManualDocument] = []
        self._doc_by_key: Dict[str, ManualDocument] = {}
        self._doc_key_by_path: Dict[Path, str] = {}

        self.window: Optional[tk.Toplevel] = None
        self.nav_tree: Optional[ttk.Treeview] = None
        self.text_widget: Optional[tk.Text] = None
        self.status_var: Optional[tk.StringVar] = None

        self._anchor_positions: Dict[str, str] = {}
        self._image_refs: List[Any] = []
        self._link_tag_counter = 0
        self._current_doc_key: Optional[str] = None
        self._resize_after_id: Optional[str] = None
        self._last_content_width = 0
        self._suspend_resize = False
        self._suspend_nav_select = False
        self._pil_image_cache: Dict[Path, Any] = {}

        self._load_documents()
        self._build_window()
        self._populate_navigation_tree()
        self._show_document("readme")

    def _load_documents(self) -> None:
        docs: List[ManualDocument] = []
        for spec in DOC_SPECS:
            path = (self.repo_root / spec.rel_path).resolve(strict=False)
            content, exists = load_markdown_document(path, title=spec.title)
            sections = extract_nav_sections(content, max_level=2)
            doc = ManualDocument(
                key=spec.key,
                title=spec.title,
                path=path,
                content=content,
                sections=sections,
                exists=exists,
            )
            docs.append(doc)

        self.documents = docs
        self._doc_by_key = {doc.key: doc for doc in docs}
        self._doc_key_by_path = {doc.path.resolve(strict=False): doc.key for doc in docs}

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.owner)
        self.window.title("User Manual")
        self.window.geometry("1100x760")
        self.window.minsize(760, 500)
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        if self.theme:
            try:
                self.theme.apply_to_widget(self.window, "main_window")
            except Exception:
                pass

        container = tk.Frame(self.window)
        container.pack(fill=tk.BOTH, expand=True)
        if self.theme:
            try:
                self.theme.apply_to_widget(container, "main_window")
            except Exception:
                pass

        paned = tk.PanedWindow(container, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 6))

        left_frame = tk.Frame(paned, width=260)
        right_frame = tk.Frame(paned)
        if self.theme:
            try:
                self.theme.apply_to_widget(left_frame, "main_window")
                self.theme.apply_to_widget(right_frame, "main_window")
            except Exception:
                pass

        paned.add(left_frame, minsize=220)
        paned.add(right_frame)

        nav_label = tk.Label(left_frame, text="Manual Contents", anchor="w")
        nav_label.pack(fill=tk.X)
        if self.theme:
            try:
                self.theme.apply_to_widget(nav_label, "label")
            except Exception:
                pass

        tree_frame = tk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.nav_tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        nav_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.nav_tree.yview)
        self.nav_tree.configure(yscrollcommand=nav_scroll.set)
        self.nav_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        nav_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        content_frame = tk.Frame(right_frame)
        content_frame.pack(fill=tk.BOTH, expand=True)
        if self.theme:
            try:
                self.theme.apply_to_widget(content_frame, "main_window")
            except Exception:
                pass

        self.text_widget = tk.Text(
            content_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            padx=12,
            pady=10,
            cursor="arrow",
        )
        y_scroll = ttk.Scrollbar(content_frame, orient="vertical", command=self.text_widget.yview)
        self.text_widget.configure(yscrollcommand=y_scroll.set)
        self.text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._configure_text_tags()

        if self.theme:
            try:
                self.theme.apply_to_widget(self.text_widget, "text")
            except Exception:
                pass

        status_frame = tk.Frame(container)
        status_frame.pack(fill=tk.X, padx=10)
        if self.theme:
            try:
                self.theme.apply_to_widget(status_frame, "status_bar")
            except Exception:
                pass

        self.status_var = tk.StringVar(value="Ready")
        status_label = tk.Label(status_frame, textvariable=self.status_var, anchor="w")
        status_label.pack(fill=tk.X)
        if self.theme:
            try:
                self.theme.apply_to_widget(status_label, "status_bar")
            except Exception:
                pass

        button_frame = tk.Frame(container)
        button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        if self.theme:
            try:
                self.theme.apply_to_widget(button_frame, "main_window")
            except Exception:
                pass

        close_btn = tk.Button(button_frame, text="Close", command=self._close)
        close_btn.pack(side=tk.RIGHT)
        if self.theme:
            try:
                self.theme.apply_to_widget(close_btn, "button_secondary")
            except Exception:
                pass

        add_shortcut_hint(button_frame, self.theme, "Esc/Ctrl+W/Cmd+W close")

        self._bind_keyboard_shortcuts()
        self.nav_tree.bind("<<TreeviewSelect>>", self._on_nav_select)
        self.text_widget.bind("<Configure>", self._on_text_resize, add="+")

        ensure_dialog_focus(self.window, self.owner)

    def _configure_text_tags(self) -> None:
        assert self.text_widget is not None
        widget = self.text_widget

        widget.tag_configure("body", spacing1=2, spacing3=6)
        widget.tag_configure("heading1", font=(None, 16, "bold"), spacing1=8, spacing3=8)
        widget.tag_configure("heading2", font=(None, 13, "bold"), spacing1=6, spacing3=6)
        widget.tag_configure("heading3", font=(None, 12, "bold"), spacing1=4, spacing3=4)
        widget.tag_configure("list", lmargin1=18, lmargin2=26, spacing3=2)
        widget.tag_configure("code", font=("Courier", 10), lmargin1=12, lmargin2=12, spacing1=2, spacing3=6)
        widget.tag_configure("table", font=("Courier", 10), lmargin1=8, lmargin2=8, spacing1=2, spacing3=6)
        widget.tag_configure("quote", lmargin1=14, lmargin2=18, spacing1=2, spacing3=4)

    def _populate_navigation_tree(self) -> None:
        assert self.nav_tree is not None
        tree = self.nav_tree
        tree.delete(*tree.get_children())

        for doc in self.documents:
            doc_id = f"doc:{doc.key}"
            tree.insert("", "end", iid=doc_id, text=doc.title, open=True)
            for level, title, anchor in doc.sections:
                prefix = "" if level == 1 else "  "
                sec_id = f"sec:{doc.key}:{anchor}"
                tree.insert(doc_id, "end", iid=sec_id, text=f"{prefix}{title}")

    def _bind_keyboard_shortcuts(self) -> None:
        bind_close_shortcuts(self.window, self._close)

    def _set_status(self, text: str) -> None:
        if self.status_var is not None:
            self.status_var.set(text)

    def _on_nav_select(self, _event: Optional[tk.Event] = None) -> None:
        if self._suspend_nav_select:
            return
        assert self.nav_tree is not None
        selected = self.nav_tree.selection()
        if not selected:
            return

        item_id = selected[0]
        if item_id.startswith("doc:"):
            key = item_id.split(":", 1)[1]
            self._show_document(key, from_nav=True)
            return
        if item_id.startswith("sec:"):
            parts = item_id.split(":", 2)
            if len(parts) != 3:
                return
            key = parts[1]
            anchor = parts[2]
            self._show_document(key, anchor=anchor, from_nav=True)

    def _show_document(self, key: str, *, anchor: Optional[str] = None, from_nav: bool = False) -> None:
        doc = self._doc_by_key.get(key)
        if doc is None:
            self._set_status("Manual section unavailable.")
            return

        self._current_doc_key = key

        self._render_markdown(doc)

        if anchor:
            self._jump_to_anchor(anchor)
        else:
            if self.text_widget is not None:
                self.text_widget.yview_moveto(0.0)
        self._set_status(f"Viewing: {doc.title}")

        if self.nav_tree is not None and not from_nav:
            node = f"doc:{doc.key}"
            if self.nav_tree.exists(node):
                current_selection = self.nav_tree.selection()
                if not current_selection or current_selection[0] != node:
                    self._suspend_nav_select = True
                    try:
                        self.nav_tree.selection_set(node)
                    finally:
                        self._suspend_nav_select = False

    def _render_markdown(self, doc: ManualDocument) -> None:
        assert self.text_widget is not None
        widget = self.text_widget

        self._suspend_resize = True
        self._image_refs = []
        self._anchor_positions = {}
        self._link_tag_counter = 0

        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)

        lines = doc.content.splitlines()
        heading_anchors = extract_all_heading_anchors(doc.content)
        heading_index = 0

        code_lines: List[str] = []
        in_code_block = False
        table_lines: List[str] = []

        def flush_code() -> None:
            nonlocal code_lines
            if not code_lines:
                return
            widget.insert(tk.END, "\n".join(code_lines) + "\n\n", ("code",))
            code_lines = []

        def flush_table() -> None:
            nonlocal table_lines
            if not table_lines:
                return
            rendered = self._render_table_lines(table_lines)
            if rendered:
                widget.insert(tk.END, rendered + "\n\n", ("table",))
            table_lines = []

        for raw_line in lines:
            line = raw_line.rstrip("\n")

            if line.strip().startswith("```"):
                flush_table()
                if in_code_block:
                    flush_code()
                    in_code_block = False
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(line)
                continue

            if line.strip() == "":
                flush_table()
                widget.insert(tk.END, "\n", ("body",))
                continue

            if line.count("|") >= 2:
                table_lines.append(line)
                continue

            flush_table()

            heading = _HEADING_RE.match(line)
            if heading:
                level = len(heading.group(1))
                text = heading.group(2).strip()
                tag = "heading1" if level == 1 else "heading2" if level == 2 else "heading3"
                insert_index = widget.index(tk.END)
                anchor = slugify_anchor(text)
                if heading_index < len(heading_anchors):
                    anchor = heading_anchors[heading_index][2]
                heading_index += 1
                self._anchor_positions[anchor] = insert_index
                widget.insert(tk.END, text + "\n", (tag,))
                continue

            image_match = _IMAGE_RE.match(line.strip())
            if image_match:
                alt_text = image_match.group(1).strip() or "image"
                target = image_match.group(2).strip()
                self._insert_image_line(target, alt_text, source_doc_path=doc.path)
                widget.insert(tk.END, "\n")
                continue

            stripped = line.lstrip()
            if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
                self._insert_inline_markdown(stripped + "\n", source_doc_path=doc.path, base_tag="list")
                continue

            if stripped.startswith(">"):
                quote_text = stripped.lstrip(">").strip()
                self._insert_inline_markdown(quote_text + "\n", source_doc_path=doc.path, base_tag="quote")
                continue

            if re.match(r"^\s*([-*_])\1\1+\s*$", stripped):
                widget.insert(tk.END, "-" * 72 + "\n", ("body",))
                continue

            self._insert_inline_markdown(line + "\n", source_doc_path=doc.path, base_tag="body")

        flush_code()
        flush_table()

        widget.configure(state=tk.DISABLED)
        self._suspend_resize = False

    def _render_table_lines(self, lines: Iterable[str]) -> str:
        rows: List[List[str]] = []
        for line in lines:
            if is_markdown_table_separator(line):
                continue
            text = line.strip()
            if "|" not in text:
                continue
            cells = [cell.strip() for cell in text.strip("|").split("|")]
            rows.append(cells)

        if not rows:
            return ""

        column_count = max(len(row) for row in rows)
        for row in rows:
            row.extend([""] * (column_count - len(row)))

        widths = [0] * column_count
        for row in rows:
            for idx, value in enumerate(row):
                widths[idx] = max(widths[idx], len(value))

        border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        output_lines = [border]
        for idx, row in enumerate(rows):
            rendered_row = "| " + " | ".join(row[col].ljust(widths[col]) for col in range(column_count)) + " |"
            output_lines.append(rendered_row)
            if idx == 0:
                output_lines.append(border)
        output_lines.append(border)
        return "\n".join(output_lines)

    def _insert_inline_markdown(self, text: str, *, source_doc_path: Path, base_tag: str) -> None:
        assert self.text_widget is not None
        cursor = 0
        for match in _LINK_RE.finditer(text):
            start, end = match.span()
            if start > cursor:
                self.text_widget.insert(tk.END, text[cursor:start], (base_tag,))

            label = match.group(1)
            target = match.group(2)
            tag_name = f"link_{self._link_tag_counter}"
            self._link_tag_counter += 1

            link_start = self.text_widget.index(tk.END)
            self.text_widget.insert(tk.END, label, (base_tag, tag_name))
            link_end = self.text_widget.index(tk.END)

            self.text_widget.tag_configure(tag_name, underline=True)
            if self.theme:
                try:
                    self.text_widget.tag_configure(tag_name, foreground=self.theme.colors.get("accent", "#4ea1ff"))
                except Exception:
                    self.text_widget.tag_configure(tag_name, foreground="#4ea1ff")
            else:
                self.text_widget.tag_configure(tag_name, foreground="#4ea1ff")

            self.text_widget.tag_bind(
                tag_name,
                "<Button-1>",
                lambda _e, t=target, p=source_doc_path: self._open_link_target(t, source_doc_path=p),
            )
            self.text_widget.tag_bind(tag_name, "<Enter>", lambda _e: self.text_widget.configure(cursor="hand2"))
            self.text_widget.tag_bind(tag_name, "<Leave>", lambda _e: self.text_widget.configure(cursor="arrow"))
            self.text_widget.tag_add(tag_name, link_start, link_end)

            cursor = end

        if cursor < len(text):
            self.text_widget.insert(tk.END, text[cursor:], (base_tag,))

    def _insert_image_line(self, target: str, alt_text: str, *, source_doc_path: Path) -> None:
        assert self.text_widget is not None

        image_path = resolve_image_path(target, source_doc_path=source_doc_path)
        if not image_path.exists():
            self.text_widget.insert(tk.END, f"[missing image: {target}]\n", ("quote",))
            return

        if Image is None or ImageTk is None:
            self.text_widget.insert(tk.END, f"[image unavailable: {target}]\n", ("quote",))
            return

        image = self._pil_image_cache.get(image_path)
        if image is None:
            try:
                with Image.open(image_path) as loaded:
                    image = loaded.copy()
                self._pil_image_cache[image_path] = image
            except Exception:
                self.text_widget.insert(tk.END, f"[failed to load image: {target}]\n", ("quote",))
                return

        try:
            work_image = image.copy()
        except Exception:
            self.text_widget.insert(tk.END, f"[failed to load image: {target}]\n", ("quote",))
            return

        max_width = self._content_image_max_width()
        width, height = compute_scaled_dimensions(work_image.width, work_image.height, max_width)
        if (width, height) != work_image.size:
            work_image = work_image.resize((width, height), Image.LANCZOS)

        photo = ImageTk.PhotoImage(work_image)
        self._image_refs.append(photo)
        self.text_widget.image_create(tk.END, image=photo)
        self.text_widget.insert(tk.END, f"\n[{alt_text}]\n", ("quote",))

    def _content_image_max_width(self) -> int:
        assert self.text_widget is not None
        width = int(self.text_widget.winfo_width() or 0)
        if width <= 200:
            try:
                width = int(self.window.winfo_width()) - 300
            except Exception:
                width = 760
        return max(220, width - 40)

    def _open_link_target(self, target: str, *, source_doc_path: Path) -> None:
        parsed = parse_markdown_link_target(target, current_doc_path=source_doc_path)

        if parsed.kind == "external" and parsed.url:
            webbrowser.open(parsed.url)
            self._set_status(f"Opened external link: {parsed.url}")
            return

        if parsed.kind == "anchor":
            self._jump_to_anchor(parsed.anchor or "section")
            return

        if parsed.kind != "doc" or parsed.target_path is None:
            return

        doc_path = parsed.target_path.resolve(strict=False)
        doc_key = self._doc_key_by_path.get(doc_path)
        if doc_key is None:
            self._set_status(f"Linked document not in manual set: {doc_path}")
            return

        self._show_document(doc_key, anchor=parsed.anchor)

    def _jump_to_anchor(self, anchor: str) -> None:
        assert self.text_widget is not None
        token = slugify_anchor(anchor)
        index = self._anchor_positions.get(token)
        if not index:
            self._set_status(f"Section not found: {anchor}")
            return
        self.text_widget.see(index)
        self.text_widget.mark_set(tk.INSERT, index)
        self._set_status(f"Jumped to: {token}")

    def _on_text_resize(self, event: tk.Event) -> None:
        if self._suspend_resize:
            return
        width = int(getattr(event, "width", 0) or 0)
        if width <= 0 or not self._current_doc_key:
            return
        if abs(width - self._last_content_width) < 35:
            return
        self._last_content_width = width

        if self._resize_after_id and self.window is not None:
            try:
                self.window.after_cancel(self._resize_after_id)
            except Exception:
                pass

        if self.window is not None:
            self._resize_after_id = self.window.after(140, self._rerender_for_resize)

    def _rerender_for_resize(self) -> None:
        self._resize_after_id = None
        if not self._current_doc_key:
            return
        doc = self._doc_by_key.get(self._current_doc_key)
        if doc is None or self.text_widget is None:
            return
        top = 0.0
        try:
            top = float(self.text_widget.yview()[0])
        except Exception:
            top = 0.0
        self._render_markdown(doc)
        self.text_widget.yview_moveto(top)

    def _close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
        self.window = None


def _manual_owner(parent: tk.Misc) -> tk.Misc:
    """Normalize singleton owner to top-level app window where possible."""
    try:
        return parent.winfo_toplevel()
    except Exception:
        return parent


def open_help_manual_dialog(parent: tk.Misc, *, theme: Any = None) -> Optional[tk.Toplevel]:
    """Open (or focus) the app-level User Manual window."""
    if parent is None:
        return None

    owner = _manual_owner(parent)
    existing = getattr(owner, "_help_manual_dialog", None)
    try:
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return existing
    except Exception:
        pass

    dialog = UserManualDialog(owner, theme=theme)
    window = dialog.window
    if window is None:
        return None

    setattr(owner, "_help_manual_dialog", window)

    def _on_destroy(_event: Optional[tk.Event] = None) -> None:
        if getattr(owner, "_help_manual_dialog", None) is window:
            setattr(owner, "_help_manual_dialog", None)

    window.bind("<Destroy>", _on_destroy, add="+")
    return window
