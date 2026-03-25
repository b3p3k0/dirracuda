"""
Dirracuda GUI Styling and Theme Management

Provides consistent styling, colors, and theme management across all GUI components.
Implements cross-platform styling with accessibility considerations.

Design Decision: Centralized styling ensures consistent appearance and makes
theme changes easy to implement across the entire application.
"""

import tkinter as tk
from tkinter import ttk
from typing import Dict, Any, Optional, List, Set
import sys


class SMBSeekTheme:
    """
    Theme manager for SMBSeek GUI.
    
    Provides consistent colors, fonts, and styling across all components.
    Handles platform-specific adjustments and accessibility options.
    
    Design Decision: Centralized theme management allows easy customization
    and ensures visual consistency throughout the application.
    """
    
    def __init__(self, use_dark_mode: bool = False):
        """
        Initialize theme manager.
        
        Args:
            use_dark_mode: Whether to use dark theme (future enhancement)
        """
        self.use_dark_mode = use_dark_mode
        self.platform = sys.platform
        
        # Color palette - inspired by security tool interfaces
        self.colors = self._define_colors()
        self.fonts = self._define_fonts()
        self.styles = self._define_component_styles()

    def _refresh_theme_definitions(self) -> None:
        """Recompute color/style dictionaries after a mode switch."""
        self.colors = self._define_colors()
        self.styles = self._define_component_styles()
    
    def _define_colors(self) -> Dict[str, str]:
        """
        Define color palette for the application.
        
        Returns:
            Dictionary mapping color names to hex values
            
        Design Decision: Professional security tool aesthetic with
        high contrast for accessibility and clear status indication.
        """
        if self.use_dark_mode:
            return {
                "primary_bg": "#1c1f24",
                "secondary_bg": "#2a2f36",
                "card_bg": "#242932",
                "border": "#3b4250",
                "text": "#f1f5fb",
                "text_secondary": "#b6c0d1",
                "success": "#5cc87a",
                "warning": "#f2b84a",
                "error": "#f87171",
                "info": "#66b3ff",
                "critical": "#ff6b6b",
                "high": "#ff9f43",
                "medium": "#f4d35e",
                "low": "#7ddf8e",
                "accent": "#3b82f6",
                "hover": "#313844",
                # Log viewer colors
                "log_bg": "#0e1218",
                "log_fg": "#e5ecf7",
                "log_placeholder": "#93a1b5",
                # ANSI terminal colors for log output
                "ansi_black": "#909cb0",
                "ansi_red": "#ff8f8f",
                "ansi_green": "#8fe6a6",
                "ansi_yellow": "#ffd979",
                "ansi_blue": "#8fc5ff",
                "ansi_magenta": "#e1a6ff",
                "ansi_cyan": "#7be4f7",
                "ansi_white": "#e5ecf7",
                "ansi_bright_black": "#aab5c8",
                "ansi_bright_red": "#ffaaaa",
                "ansi_bright_green": "#a7f3be",
                "ansi_bright_yellow": "#ffe59f",
                "ansi_bright_blue": "#acd4ff",
                "ansi_bright_magenta": "#ebbcff",
                "ansi_bright_cyan": "#9cf0ff",
                "ansi_bright_white": "#ffffff"
            }
        else:
            return {
                # Light theme - current implementation
                "primary_bg": "#ffffff",
                "secondary_bg": "#f5f5f5",
                "card_bg": "#fafafa",
                "border": "#e0e0e0",
                "text": "#212121",
                "text_secondary": "#666666",
                "success": "#4caf50",
                "warning": "#ff9800",
                "error": "#f44336",
                "info": "#2196f3",
                "critical": "#d32f2f",
                "high": "#f57c00",
                "medium": "#fbc02d",
                "low": "#689f38",
                "accent": "#1976d2",
                "hover": "#e3f2fd",
                # Log viewer colors (dark terminal-style background)
                "log_bg": "#111418",
                "log_fg": "#f5f5f5",
                "log_placeholder": "#9ea4b3",
                # ANSI terminal colors for log output
                "ansi_black": "#7f8796",
                "ansi_red": "#ff7676",
                "ansi_green": "#7dd87d",
                "ansi_yellow": "#ffd666",
                "ansi_blue": "#76b9ff",
                "ansi_magenta": "#d692ff",
                "ansi_cyan": "#4dd0e1",
                "ansi_white": "#f5f5f5",
                "ansi_bright_black": "#a0a7b4",
                "ansi_bright_red": "#ff8b8b",
                "ansi_bright_green": "#8ef79a",
                "ansi_bright_yellow": "#ffe082",
                "ansi_bright_blue": "#90c8ff",
                "ansi_bright_magenta": "#f78bff",
                "ansi_bright_cyan": "#6fe8ff",
                "ansi_bright_white": "#ffffff"
            }
    
    def _define_fonts(self) -> Dict[str, tuple]:
        """
        Define font families and sizes.
        
        Returns:
            Dictionary mapping font purposes to (family, size, weight) tuples
            
        Design Decision: Platform-appropriate fonts with good readability
        for security data and sufficient size hierarchy.
        """
        # Platform-specific font preferences
        if self.platform == "darwin":  # macOS
            default_family = "SF Pro Display"
            mono_family = "SF Mono"
        elif self.platform == "win32":  # Windows
            default_family = "Segoe UI"
            mono_family = "Consolas"
        else:  # Linux and others
            default_family = "Ubuntu"
            mono_family = "Ubuntu Mono"
        
        return {
            "title": (default_family, 18, "bold"),
            "heading": (default_family, 14, "bold"),
            "body": (default_family, 10, "normal"),
            "small": (default_family, 9, "normal"),
            "mono": (mono_family, 10, "normal"),
            "mono_small": (mono_family, 9, "normal"),
            "button": (default_family, 10, "normal"),
            "status": (default_family, 9, "normal")
        }
    
    def _define_component_styles(self) -> Dict[str, Dict[str, Any]]:
        """
        Define styling for specific component types.
        
        Returns:
            Dictionary mapping component types to style dictionaries
        """
        return {
            "main_window": {
                "bg": self.colors["primary_bg"],
                "relief": "flat"
            },
            "card": {
                "bg": self.colors["card_bg"],
                "relief": "solid",
                "borderwidth": 1
            },
            "metric_card": {
                "bg": self.colors["card_bg"],
                "relief": "solid",
                "borderwidth": 1,
                "padx": 15,
                "pady": 15,
                "cursor": "hand2"
            },
            "button_primary": {
                "bg": self.colors["accent"],
                "fg": "white",
                "relief": "flat",
                "borderwidth": 0,
                "cursor": "hand2",
                "font": self.fonts["button"]
            },
            "button_secondary": {
                "bg": self.colors["secondary_bg"],
                "fg": self.colors["text"],
                "relief": "solid",
                "borderwidth": 1,
                "highlightthickness": 1,
                "highlightbackground": self.colors["border"],
                "highlightcolor": self.colors["border"],
                "activebackground": self.colors["hover"],
                "activeforeground": self.colors["text"],
                "cursor": "hand2",
                "font": self.fonts["button"]
            },
            "button_danger": {
                "bg": self.colors["error"],
                "fg": "white",
                "relief": "flat",
                "borderwidth": 0,
                "cursor": "hand2",
                "font": self.fonts["button"]
            },
            "button_disabled": {
                "bg": "#cccccc",
                "fg": "#666666",
                "relief": "flat",
                "borderwidth": 0,
                "cursor": "arrow",
                "font": self.fonts["button"]
            },
            "status_bar": {
                "bg": self.colors["secondary_bg"],
                "fg": self.colors["text_secondary"],
                "relief": "sunken",
                "borderwidth": 1,
                "font": self.fonts["status"]
            },
            "progress_bar": {
                "troughcolor": self.colors["secondary_bg"],
                "background": self.colors["accent"],
                "borderwidth": 0,
                "relief": "flat"
            },
            "label": {
                "bg": self.colors["primary_bg"],
                "fg": self.colors["text"]
            },
            "text": {
                "bg": self.colors["primary_bg"],
                "fg": self.colors["text"]
            },
            "checkbox": {
                "bg": self.colors["card_bg"],
                "fg": self.colors["text"],
                "activebackground": self.colors["card_bg"],
                "activeforeground": self.colors["text"],
                "selectcolor": self.colors["secondary_bg"]
            },
            "entry": {
                "bg": self.colors["secondary_bg"],
                "fg": self.colors["text"],
                "insertbackground": self.colors["text"],
                "relief": "solid",
                "borderwidth": 1,
                "highlightthickness": 1,
                "highlightbackground": self.colors["border"],
                "highlightcolor": self.colors["accent"]
            },
            "listbox": {
                "bg": self.colors["secondary_bg"],
                "fg": self.colors["text"],
                "selectbackground": self.colors["accent"],
                "selectforeground": "#ffffff",
                "relief": "solid",
                "borderwidth": 1,
                "highlightthickness": 1,
                "highlightbackground": self.colors["border"],
                "highlightcolor": self.colors["border"]
            },
            "text_area": {
                "bg": self.colors["secondary_bg"],
                "fg": self.colors["text"],
                "insertbackground": self.colors["text"],
                "selectbackground": self.colors["accent"],
                "selectforeground": "#ffffff",
                "relief": "solid",
                "borderwidth": 1,
                "highlightthickness": 1,
                "highlightbackground": self.colors["border"],
                "highlightcolor": self.colors["border"]
            },
        }

    def get_mode(self) -> str:
        """Return active theme mode as 'light' or 'dark'."""
        return "dark" if self.use_dark_mode else "light"

    def set_mode(self, mode: str, root: Optional[tk.Widget] = None) -> str:
        """
        Set theme mode and optionally repaint existing windows.

        Args:
            mode: Desired mode ('light' or 'dark'). Any other value falls back to 'light'.
            root: Optional root widget used for immediate repaint.
        """
        normalized = (mode or "light").strip().lower()
        next_dark = normalized == "dark"

        if next_dark == self.use_dark_mode:
            if root is not None:
                self.apply_theme_to_application(root)
            return self.get_mode()

        old_colors = dict(self.colors)
        self.use_dark_mode = next_dark
        self._refresh_theme_definitions()

        if root is not None:
            self.apply_theme_to_application(root, old_colors=old_colors)

        return self.get_mode()

    def toggle_mode(self, root: Optional[tk.Widget] = None) -> str:
        """Toggle between light/dark mode and optionally repaint existing windows."""
        old_colors = dict(self.colors)
        self.use_dark_mode = not self.use_dark_mode
        self._refresh_theme_definitions()
        if root is not None:
            self.apply_theme_to_application(root, old_colors=old_colors)
        return self.get_mode()

    def _collect_open_windows(self, root: tk.Widget) -> List[tk.Widget]:
        """Return the root window plus any currently-open Toplevel windows."""
        try:
            main_window = root.winfo_toplevel()
        except Exception:
            return []

        windows = [main_window]
        seen = {str(main_window)}
        stack = [main_window]

        while stack:
            parent = stack.pop()
            try:
                children = parent.winfo_children()
            except tk.TclError:
                continue

            for child in children:
                try:
                    widget_class = child.winfo_class()
                except tk.TclError:
                    continue

                if widget_class in ("Tk", "Toplevel"):
                    key = str(child)
                    if key not in seen:
                        seen.add(key)
                        windows.append(child)
                        stack.append(child)

        return windows

    @staticmethod
    def _normalize_color(value: Any) -> Optional[str]:
        """Normalize color values for safe equality checks."""
        if not isinstance(value, str):
            return None
        return value.strip().lower()

    def _retarget_widget_colors(
        self,
        widget: tk.Widget,
        old_colors: Dict[str, str],
        new_colors: Dict[str, str],
    ) -> None:
        """Replace old-theme colors and normalize unthemed Tk defaults."""
        color_swaps = {}
        for name, old_value in old_colors.items():
            new_value = new_colors.get(name)
            if isinstance(old_value, str) and isinstance(new_value, str) and old_value != new_value:
                color_swaps[self._normalize_color(old_value)] = new_value

        options = (
            "background",
            "bg",
            "foreground",
            "fg",
            "insertbackground",
            "selectbackground",
            "selectforeground",
            "disabledforeground",
            "activebackground",
            "activeforeground",
            "highlightbackground",
            "highlightcolor",
            "troughcolor",
            "fieldbackground",
        )

        option_values: Dict[str, Optional[str]] = {}
        for option in options:
            try:
                current_value = widget.cget(option)
            except tk.TclError:
                continue
            option_values[option] = self._normalize_color(current_value)

            if not color_swaps:
                continue

            replacement = color_swaps.get(option_values[option])
            if replacement:
                try:
                    widget.configure(**{option: replacement})
                    option_values[option] = self._normalize_color(replacement)
                except tk.TclError:
                    continue

        # Normalize default Tk colors that were never explicitly themed.
        default_light_bg = {"white", "#ffffff", "#fff", "systemwindow", "systembuttonface", "#d9d9d9", "gray90", "grey90"}
        default_dark_fg = {"black", "#000000", "systemwindowtext", "systembuttontext"}
        default_light_border = {"white", "#ffffff", "#fff", "#d9d9d9", "gray90", "grey90", "systembuttonface"}
        widget_type = ""
        try:
            widget_type = widget.winfo_class()
        except tk.TclError:
            return

        def _get(opt: str) -> Optional[str]:
            return option_values.get(opt)

        def _set(opt: str, color: str) -> None:
            try:
                widget.configure(**{opt: color})
                option_values[opt] = self._normalize_color(color)
            except tk.TclError:
                return

        def _set_if_in(opt: str, values: Set[str], color: str) -> None:
            current = _get(opt)
            if current in values:
                _set(opt, color)

        if widget_type in {"Label", "Message", "Checkbutton", "Radiobutton"}:
            _set_if_in("fg", default_dark_fg, self.colors["text"])
            _set_if_in("foreground", default_dark_fg, self.colors["text"])
            _set_if_in("bg", default_light_bg, self.colors["primary_bg"])
            _set_if_in("background", default_light_bg, self.colors["primary_bg"])

        if widget_type in {"Button"}:
            _set_if_in("fg", default_dark_fg, self.colors["text"])
            _set_if_in("foreground", default_dark_fg, self.colors["text"])
            _set_if_in("bg", default_light_bg, self.colors["secondary_bg"])
            _set_if_in("background", default_light_bg, self.colors["secondary_bg"])
            _set_if_in("highlightbackground", default_light_border, self.colors["border"])
            _set_if_in("highlightcolor", default_light_border, self.colors["border"])

        if widget_type in {"Entry", "Text", "Listbox", "Spinbox"}:
            _set_if_in("fg", default_dark_fg, self.colors["text"])
            _set_if_in("foreground", default_dark_fg, self.colors["text"])
            _set_if_in("bg", default_light_bg, self.colors["secondary_bg"])
            _set_if_in("background", default_light_bg, self.colors["secondary_bg"])
            _set_if_in("insertbackground", default_dark_fg, self.colors["text"])
            _set_if_in("highlightbackground", default_light_border, self.colors["border"])
            _set_if_in("highlightcolor", default_light_border, self.colors["border"])

        if widget_type in {"Frame", "Canvas"}:
            _set_if_in("bg", default_light_bg, self.colors["primary_bg"])
            _set_if_in("background", default_light_bg, self.colors["primary_bg"])

        if widget_type == "LabelFrame":
            _set_if_in("bg", default_light_bg, self.colors["primary_bg"])
            _set_if_in("background", default_light_bg, self.colors["primary_bg"])
            _set_if_in("fg", default_dark_fg, self.colors["text"])
            _set_if_in("foreground", default_dark_fg, self.colors["text"])

    def _retarget_widget_tree(
        self,
        root_widget: tk.Widget,
        old_colors: Dict[str, str],
        new_colors: Dict[str, str],
    ) -> None:
        """Repaint all descendants using old->new color mapping."""
        stack = [root_widget]

        while stack:
            widget = stack.pop()
            try:
                self._retarget_widget_colors(widget, old_colors, new_colors)
                stack.extend(widget.winfo_children())
            except tk.TclError:
                continue

    def apply_theme_to_application(self, root: tk.Widget, old_colors: Optional[Dict[str, str]] = None) -> None:
        """
        Apply current theme to all open app windows and dialog trees.

        Args:
            root: Any widget within the app (typically dashboard/root window).
            old_colors: Optional palette before mode switch for targeted repaint.
        """
        windows = self._collect_open_windows(root)
        if not windows:
            return

        previous = old_colors or {}
        for window in windows:
            try:
                self.apply_to_widget(window, "main_window")
                self.setup_ttk_styles(window)
                self._retarget_widget_tree(window, previous, self.colors)
            except tk.TclError:
                continue
    
    def apply_to_widget(self, widget: tk.Widget, style_name: str) -> None:
        """
        Apply named style to a widget.
        
        Args:
            widget: Tkinter widget to style
            style_name: Name of style from styles dictionary
        """
        if style_name in self.styles:
            style_dict = self.styles[style_name].copy()
            widget_type = widget.winfo_class() if hasattr(widget, 'winfo_class') else type(widget).__name__

            # Special handling for Toplevel windows
            if widget_type in ["Toplevel", "Tk"]:
                # Only set background color for window
                bg = style_dict.get("bg") or style_dict.get("background")
                if bg:
                    try:
                        widget.configure(background=bg)
                    except Exception:
                        try:
                            widget['background'] = bg
                        except Exception:
                            pass
                return

            # Remove options that don't apply to all widgets
            if widget_type == "Frame":
                style_dict.pop("fg", None)
            # ...existing code for other widget types...
            try:
                widget.configure(**style_dict)
            except tk.TclError as e:
                for key, value in style_dict.items():
                    try:
                        widget.configure(**{key: value})
                    except tk.TclError:
                        continue
    
    def get_severity_color(self, severity: str) -> str:
        """
        Get color for security vulnerability severity.
        
        Args:
            severity: Severity level (critical, high, medium, low)
            
        Returns:
            Hex color code for the severity level
        """
        severity_lower = severity.lower()
        if severity_lower in self.colors:
            return self.colors[severity_lower]
        else:
            return self.colors["text_secondary"]  # Default for unknown
    
    def get_status_color(self, is_success: bool) -> str:
        """
        Get color for status indication.
        
        Args:
            is_success: Whether status represents success or failure
            
        Returns:
            Hex color code for status
        """
        return self.colors["success"] if is_success else self.colors["error"]
    
    def create_hover_effect(self, widget: tk.Widget, hover_bg: Optional[str] = None) -> None:
        """
        Add hover effect to a widget.
        
        Args:
            widget: Widget to add hover effect to
            hover_bg: Background color on hover (default: theme hover color)
        """
        original_bg = widget.cget("bg")
        hover_color = hover_bg or self.colors["hover"]
        
        def on_enter(event):
            widget.configure(bg=hover_color)
        
        def on_leave(event):
            widget.configure(bg=original_bg)
        
        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)
    
    def setup_ttk_styles(self, root: tk.Tk) -> None:
        """
        Configure ttk styles for themed widgets.
        
        Args:
            root: Root tkinter window
            
        Design Decision: Use ttk for progress bars and other complex widgets
        that benefit from native theming while maintaining custom colors.
        """
        style = ttk.Style(root)

        def _safe_configure(style_name: str, **kwargs) -> None:
            try:
                style.configure(style_name, **kwargs)
            except tk.TclError:
                return

        def _safe_map(style_name: str, **kwargs) -> None:
            try:
                style.map(style_name, **kwargs)
            except tk.TclError:
                return

        # Configure progress bar style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        _safe_configure(
            "SMBSeek.Horizontal.TProgressbar",
            **self.styles["progress_bar"]
        )

        # Generic ttk widget defaults used across dialogs/windows.
        _safe_configure("TFrame", background=self.colors["primary_bg"])
        _safe_configure("TLabel", background=self.colors["primary_bg"], foreground=self.colors["text"])
        _safe_configure("TLabelframe", background=self.colors["primary_bg"], bordercolor=self.colors["border"])
        _safe_configure("TLabelframe.Label", background=self.colors["primary_bg"], foreground=self.colors["text"])
        _safe_configure(
            "TButton",
            background=self.colors["secondary_bg"],
            foreground=self.colors["text"],
            bordercolor=self.colors["border"],
        )
        _safe_map(
            "TButton",
            background=[("active", self.colors["hover"]), ("disabled", self.colors["secondary_bg"])],
            foreground=[("disabled", self.colors["text_secondary"])],
        )
        _safe_configure(
            "TEntry",
            fieldbackground=self.colors["secondary_bg"],
            foreground=self.colors["text"],
            insertcolor=self.colors["text"],
            bordercolor=self.colors["border"],
        )
        _safe_map(
            "TEntry",
            fieldbackground=[("disabled", self.colors["card_bg"])],
            foreground=[("disabled", self.colors["text_secondary"])],
        )
        _safe_configure(
            "TCombobox",
            fieldbackground=self.colors["secondary_bg"],
            background=self.colors["secondary_bg"],
            foreground=self.colors["text"],
            arrowcolor=self.colors["text"],
            bordercolor=self.colors["border"],
        )
        _safe_map(
            "TCombobox",
            fieldbackground=[("readonly", self.colors["secondary_bg"])],
            background=[("readonly", self.colors["secondary_bg"])],
            foreground=[("readonly", self.colors["text"])],
        )
        _safe_configure("TCheckbutton", background=self.colors["primary_bg"], foreground=self.colors["text"])
        _safe_map(
            "TCheckbutton",
            background=[("active", self.colors["primary_bg"])],
            foreground=[("disabled", self.colors["text_secondary"])],
        )
        _safe_configure("TRadiobutton", background=self.colors["primary_bg"], foreground=self.colors["text"])
        _safe_map(
            "TRadiobutton",
            background=[("active", self.colors["primary_bg"])],
            foreground=[("disabled", self.colors["text_secondary"])],
        )
        _safe_configure("TNotebook", background=self.colors["primary_bg"], borderwidth=0)
        _safe_configure(
            "TNotebook.Tab",
            background=self.colors["secondary_bg"],
            foreground=self.colors["text"],
            padding=(8, 4),
        )
        _safe_map(
            "TNotebook.Tab",
            background=[("selected", self.colors["card_bg"]), ("active", self.colors["hover"])],
            foreground=[("selected", self.colors["text"])],
        )
        _safe_configure(
            "Treeview",
            background=self.colors["primary_bg"],
            foreground=self.colors["text"],
            fieldbackground=self.colors["primary_bg"],
            bordercolor=self.colors["border"],
        )
        _safe_map(
            "Treeview",
            background=[("selected", self.colors["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        _safe_configure(
            "Treeview.Heading",
            background=self.colors["secondary_bg"],
            foreground=self.colors["text"],
            relief="flat",
        )
        _safe_map("Treeview.Heading", background=[("active", self.colors["hover"])])
        _safe_configure(
            "TScrollbar",
            background=self.colors["secondary_bg"],
            troughcolor=self.colors["primary_bg"],
            bordercolor=self.colors["border"],
            arrowcolor=self.colors["text"],
        )
        _safe_map(
            "TScrollbar",
            background=[("active", self.colors["hover"])],
            arrowcolor=[("active", self.colors["text"])],
        )
        _safe_configure(
            "Vertical.TScrollbar",
            background=self.colors["secondary_bg"],
            troughcolor=self.colors["primary_bg"],
            bordercolor=self.colors["border"],
            arrowcolor=self.colors["text"],
        )
        _safe_map("Vertical.TScrollbar", background=[("active", self.colors["hover"])])
        _safe_configure(
            "Horizontal.TScrollbar",
            background=self.colors["secondary_bg"],
            troughcolor=self.colors["primary_bg"],
            bordercolor=self.colors["border"],
            arrowcolor=self.colors["text"],
        )
        _safe_map("Horizontal.TScrollbar", background=[("active", self.colors["hover"])])

        # Custom project button style retained for explicit SMBSeek-styled ttk buttons.
        _safe_configure(
            "SMBSeek.TButton",
            background=self.colors["accent"],
            foreground="white",
            borderwidth=0,
            focuscolor="none"
        )
        _safe_map("SMBSeek.TButton", background=[("active", self.colors["info"])])
    
    def get_icon_symbol(self, icon_type: str) -> str:
        """
        Get Unicode symbol for common icons.
        
        Args:
            icon_type: Type of icon needed
            
        Returns:
            Unicode symbol string
            
        Design Decision: Use Unicode symbols instead of image files
        for simplicity and cross-platform compatibility.
        """
        icons = {
            "success": "✓",
            "error": "✗",
            "warning": "⚠",
            "info": "ℹ",
            "scan": "🔍",
            "database": "🗄",
            "settings": "⚙",
            "report": "📊",
            "server": "🖥",
            "share": "📁",
            "vulnerability": "🔴",
            "country": "🌍",
            "time": "⏰",
            "arrow_right": "→",
            "arrow_down": "↓",
            "refresh": "🔄"
        }
        
        return icons.get(icon_type, "•")
    
    def create_separator(self, parent: tk.Widget, orientation: str = "horizontal") -> ttk.Separator:
        """
        Create styled separator widget.
        
        Args:
            parent: Parent widget
            orientation: "horizontal" or "vertical"
            
        Returns:
            Configured separator widget
        """
        separator = ttk.Separator(parent, orient=orientation)
        return separator
    
    def create_styled_label(self, parent: tk.Widget, text: str, 
                           style_type: str = "body", **kwargs) -> tk.Label:
        """
        Create label with theme styling.
        
        Args:
            parent: Parent widget
            text: Label text
            style_type: Font style type from fonts dictionary
            **kwargs: Additional label options
            
        Returns:
            Configured label widget
        """
        font_config = self.fonts.get(style_type, self.fonts["body"])
        
        # Extract fg from kwargs if present, otherwise use theme default
        fg_color = kwargs.pop("fg", self.colors["text"])
        
        label = tk.Label(
            parent,
            text=text,
            font=font_config,
            bg=self.colors["primary_bg"],
            fg=fg_color,
            **kwargs
        )
        
        return label
    
    def create_metric_card_frame(self, parent: tk.Widget) -> tk.Frame:
        """
        Create styled frame for metric cards.
        
        Args:
            parent: Parent widget
            
        Returns:
            Configured frame widget for metric display
        """
        frame = tk.Frame(parent)
        self.apply_to_widget(frame, "metric_card")
        self.create_hover_effect(frame)
        
        return frame


# Global theme instance
theme = SMBSeekTheme()


def get_theme() -> SMBSeekTheme:
    """Get the global theme instance."""
    return theme


def apply_theme_to_window(window: tk.Tk) -> None:
    """
    Apply theme to main window.
    
    Args:
        window: Main application window
    """
    theme.apply_to_widget(window, "main_window")
    theme.setup_ttk_styles(window)
