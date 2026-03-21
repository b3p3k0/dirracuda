"""
Read-only image viewer for xsmbseek.

Features:
- Displays common raster formats (JPEG/PNG/GIF/WebP/BMP/TIFF) using Pillow.
- Enforces safety caps on image dimensions (max_pixels) and uses already
  size-limited bytes provided by the caller.
- Fit-to-window scaling with resize handling.
"""

import io
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

try:
    from PIL import Image, ImageTk
except Exception as e:  # pragma: no cover - runtime dependency
    Image = None
    ImageTk = None
    _PIL_IMPORT_ERROR = e
else:
    _PIL_IMPORT_ERROR = None

try:
    from gui.utils.style import get_theme
except ImportError:
    from utils.style import get_theme

try:
    from gui.utils.dialog_helpers import ensure_dialog_focus
except ImportError:
    from utils.dialog_helpers import ensure_dialog_focus


def open_image_viewer(
    parent: tk.Widget,
    file_path: str,
    content: bytes,
    max_pixels: int,
    theme=None,
    on_save_callback: Optional[Callable[[], None]] = None,
    truncated: bool = False,
) -> None:
    """Convenience wrapper to open ImageViewerWindow."""
    ImageViewerWindow(
        parent=parent,
        file_path=file_path,
        content=content,
        max_pixels=max_pixels,
        theme=theme,
        on_save_callback=on_save_callback,
        truncated=truncated,
    )


class ImageViewerWindow:
    """Simple, safe image viewer."""

    def __init__(
        self,
        parent: tk.Widget,
        file_path: str,
        content: bytes,
        max_pixels: int,
        theme=None,
        on_save_callback: Optional[Callable[[], None]] = None,
        truncated: bool = False,
    ) -> None:
        if Image is None or ImageTk is None:
            raise RuntimeError(f"Pillow is required for image viewing: {_PIL_IMPORT_ERROR}")

        self.parent = parent
        self.file_path = file_path
        self.theme = theme or get_theme()
        self.on_save_callback = on_save_callback
        self.truncated = truncated
        self.max_pixels = max_pixels

        # Load image safely
        self.original_image = self._load_image_safe(content)
        self.photo_image = None

        self.window: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.status_var: Optional[tk.StringVar] = None

        self._build_window()
        self._render_image()

    def _load_image_safe(self, content: bytes) -> Image.Image:
        """Decode image with pixel guard."""
        bio = io.BytesIO(content)
        img = Image.open(bio)
        img.load()
        w, h = img.size
        if w * h > self.max_pixels:
            raise RuntimeError(f"Image too large ({w}x{h}). Limit: {self.max_pixels:,} pixels.")
        return img

    def _build_window(self) -> None:
        self.window = tk.Toplevel(self.parent)
        self.window.title(f"Image Viewer - {self.file_path}")
        self.window.geometry("900x700")
        self.window.minsize(400, 300)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.theme:
            self.theme.apply_to_widget(self.window, "main_window")

        # Toolbar
        toolbar = tk.Frame(self.window)
        toolbar.pack(fill=tk.X, padx=10, pady=(10, 5))
        if self.theme:
            self.theme.apply_to_widget(toolbar, "main_window")

        save_btn = tk.Button(toolbar, text="⬇ Save to Quarantine", command=self._on_save)
        save_btn.pack(side=tk.LEFT)
        if self.theme:
            self.theme.apply_to_widget(save_btn, "button_secondary")
        if self.on_save_callback is None:
            save_btn.configure(state=tk.DISABLED)

        info_text = f"{self.original_image.width}x{self.original_image.height}"
        if self.truncated:
            info_text += " (truncated input)"
        info_label = tk.Label(toolbar, text=info_text)
        info_label.pack(side=tk.LEFT, padx=(10, 0))
        if self.theme:
            self.theme.apply_to_widget(info_label, "label")

        # Canvas with scroll + resize fit
        canvas_frame = tk.Frame(self.window)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        if self.theme:
            self.theme.apply_to_widget(canvas_frame, "main_window")

        self.canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        if self.theme:
            try:
                self.canvas.configure(bg=self.theme.colors["secondary_bg"])
            except tk.TclError:
                pass
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda e: self._render_image())

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = tk.Label(self.window, textvariable=self.status_var, anchor="w")
        status.pack(fill=tk.X, padx=10, pady=(0, 10))
        if self.theme:
            self.theme.apply_to_widget(status, "status_bar")

        if self.theme:
            self.theme.apply_theme_to_application(self.window)

    def _render_image(self) -> None:
        if not self.canvas:
            return
        canvas_w = self.canvas.winfo_width() or 1
        canvas_h = self.canvas.winfo_height() or 1
        img_w, img_h = self.original_image.size
        scale = min(canvas_w / img_w, canvas_h / img_h, 1.0)
        target_size = (max(1, int(img_w * scale)), max(1, int(img_h * scale)))
        img = self.original_image if scale == 1.0 else self.original_image.resize(target_size, Image.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        x = (canvas_w - target_size[0]) // 2
        y = (canvas_h - target_size[1]) // 2
        self.canvas.create_image(x, y, anchor="nw", image=self.photo_image)
        self.status_var.set(f"{img_w}x{img_h}  |  display {target_size[0]}x{target_size[1]}")

    def _on_save(self) -> None:
        if callable(self.on_save_callback):
            self.on_save_callback()
            self.status_var.set("Saved to quarantine.")

    def _on_close(self) -> None:
        try:
            self.window.destroy()
        except Exception:
            pass
        self.window = None
