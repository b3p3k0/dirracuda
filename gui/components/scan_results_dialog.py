"""
Dirracuda Scan Results Dialog

Displays scan completion results with summary statistics and navigation options.
Handles successful, interrupted, and failed scan scenarios with appropriate messaging.

Design Decision: Modal results dialog ensures users see scan outcomes
and provides clear options for viewing detailed results or returning to main interface.
"""

import tkinter as tk
from tkinter import ttk
import webbrowser
from datetime import datetime, timedelta
from typing import Dict, Any, Callable, Optional
import sys
import os

from gui.utils import safe_messagebox as messagebox
from gui.utils.style import get_theme
from gui.utils.dialog_helpers import ensure_dialog_focus


class ScanResultsDialog:
    """
    Modal dialog displaying scan results and statistics.
    
    Shows:
    - Scan completion status (success/interrupted/failed)
    - Summary statistics (hosts scanned, accessible, etc.)
    - Scan duration and timing information
    - Error details for failed scans
    
    Design Pattern: Status-aware dialog that adapts content
    based on scan outcome while maintaining consistent interface.
    """
    
    def __init__(self, parent: tk.Widget, scan_results: Dict[str, Any]):
        """
        Initialize scan results dialog.
        
        Args:
            parent: Parent widget
            scan_results: Dictionary containing scan results and metadata
        """
        self.parent = parent
        self.scan_results = scan_results
        self.theme = get_theme()
        self.protocol = str(scan_results.get("protocol", "smb")).lower()
        
        # Dialog result
        self.result = None
        
        # UI components
        self.dialog = None
        self.close_button = None
        
        self._create_dialog()

    def _is_ftp_scan(self) -> bool:
        """Return True when rendering results for an FTP scan."""
        return self.protocol == "ftp"

    def _success_subtitle(self) -> str:
        """Protocol-aware success subtitle text."""
        if self._is_ftp_scan():
            return "FTP scan has finished successfully."
        return "SMB security scan has finished successfully."

    def _shares_label(self) -> str:
        """Protocol-aware label for the final summary metric."""
        if self._is_ftp_scan():
            return "Directories Found:"
        return "Shares Found:"

    def _access_phrase(self) -> str:
        """Protocol-aware phrase describing successful access."""
        if self._is_ftp_scan():
            return "accessible FTP directories"
        return "accessible SMB shares"
    
    def _create_dialog(self) -> None:
        """Create the scan results dialog."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Scan Results")
        self.dialog.geometry("600x625")
        self.dialog.minsize(500, 400)
        
        # Apply theme
        self.theme.apply_to_widget(self.dialog, "main_window")
        
        # Make modal
        self.dialog.transient(self.parent)
        
        # Center dialog
        self._center_dialog()
        
        # Build UI based on scan status
        self._create_header()
        self._create_summary_section()
        self._create_details_section()
        self._create_button_panel()
        
        # Setup event handlers
        self._setup_event_handlers()
        
        # Ensure window is fully rendered before grabbing
        self.dialog.update_idletasks()
        self.dialog.grab_set()

        # Ensure dialog appears on top and gains focus (critical for VMs)
        ensure_dialog_focus(self.dialog, self.parent)

        # Focus on close button
        self._focus_close_button()

    def _center_dialog(self) -> None:
        """Center dialog on parent window."""
        self.dialog.update_idletasks()
        
        # Get parent position and size
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()
        
        # Calculate center position
        width = self.dialog.winfo_width()
        height = self.dialog.winfo_height()
        x = parent_x + (parent_width // 2) - (width // 2)
        y = parent_y + (parent_height // 2) - (height // 2)
        
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")
    
    def _create_header(self) -> None:
        """Create dialog header with status-appropriate title and icon."""
        header_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(header_frame, "main_window")
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        # Determine status-specific content
        status = self.scan_results.get("status", "unknown")
        
        if status == "completed":
            icon = "✅"
            title = "Scan Completed Successfully"
            subtitle = self._success_subtitle()
            title_color = self.theme.colors["success"]
        elif status == "interrupted":
            icon = "⚠️"
            title = "Scan Interrupted"
            subtitle = "Scan was interrupted but partial results were saved."
            title_color = self.theme.colors["warning"]
        elif status == "error" or status == "failed":
            icon = "❌"
            title = "Scan Failed"
            subtitle = "Scan encountered an error and could not complete."
            title_color = self.theme.colors["error"]
        else:
            icon = "ℹ️"
            title = "Scan Results"
            subtitle = "Scan has finished with unknown status."
            title_color = self.theme.colors["text"]
        
        # Icon and title
        title_container = tk.Frame(header_frame)
        self.theme.apply_to_widget(title_container, "main_window")
        title_container.pack(anchor="w")
        
        icon_label = self.theme.create_styled_label(
            title_container,
            icon,
            "title"
        )
        icon_label.pack(side=tk.LEFT, padx=(0, 10))
        
        title_label = self.theme.create_styled_label(
            title_container,
            title,
            "heading",
            fg=title_color
        )
        title_label.pack(side=tk.LEFT)
        
        # Subtitle
        subtitle_label = self.theme.create_styled_label(
            header_frame,
            subtitle,
            "body",
            fg=self.theme.colors["text_secondary"]
        )
        subtitle_label.pack(anchor="w", pady=(5, 0))
    
    def _create_summary_section(self) -> None:
        """Create scan summary statistics section."""
        summary_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(summary_frame, "card")
        summary_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Section title
        summary_title = self.theme.create_styled_label(
            summary_frame,
            "Scan Summary",
            "heading"
        )
        summary_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # Statistics grid
        stats_frame = tk.Frame(summary_frame)
        self.theme.apply_to_widget(stats_frame, "card")
        stats_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        # Configure grid
        for i in range(2):
            stats_frame.columnconfigure(i, weight=1)
        
        # Extract statistics
        duration = self._format_duration()
        scan_time = self._format_scan_time()
        country = self.scan_results.get("country", "Global")
        hosts_scanned = self.scan_results.get("hosts_scanned", 0)
        accessible_hosts = self.scan_results.get("accessible_hosts", 0)
        shares_found = self.scan_results.get("shares_found", 0)
        
        # Create statistics display
        stats = [
            ("Scan Target:", country if country else "Global"),
            ("Duration:", duration),
            ("Hosts Scanned:", f"{hosts_scanned:,}"),
            ("Accessible Hosts:", f"{accessible_hosts:,}"),
            (self._shares_label(), f"{shares_found:,}"),
            ("Completion Time:", scan_time)
        ]
        
        # Display statistics in grid
        for i, (label, value) in enumerate(stats):
            row = i // 2
            col = i % 2
            
            stat_frame = tk.Frame(stats_frame)
            self.theme.apply_to_widget(stat_frame, "card")
            stat_frame.grid(row=row, column=col, sticky="w", padx=10, pady=2)
            
            label_widget = self.theme.create_styled_label(
                stat_frame,
                label,
                "small",
                fg=self.theme.colors["text_secondary"]
            )
            label_widget.pack(anchor="w")
            
            value_widget = self.theme.create_styled_label(
                stat_frame,
                str(value),
                "body"
            )
            value_widget.pack(anchor="w")
    
    def _create_details_section(self) -> None:
        """Create details section with status-specific information."""
        details_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(details_frame, "card")
        details_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))
        
        # Section title
        details_title = self.theme.create_styled_label(
            details_frame,
            "Details",
            "heading"
        )
        details_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # Status-specific details
        status = self.scan_results.get("status", "unknown")
        
        if status == "completed":
            self._create_success_details(details_frame)
        elif status == "interrupted":
            self._create_interruption_details(details_frame)
        elif status == "error" or status == "failed":
            self._create_error_details(details_frame)
        else:
            self._create_unknown_details(details_frame)
    
    def _create_success_details(self, parent: tk.Widget) -> None:
        """Create details for successful scan completion."""
        # Check if we have an enhanced summary message from scan results
        summary_message = self.scan_results.get("summary_message")

        if summary_message:
            # Use enhanced summary message if available
            details_text = f"{summary_message}\n\n"
            details_text += f"Discovered {self.scan_results.get('accessible_hosts', 0)} servers with accessible "
            details_text += f"{self._access_phrase()} out of {self.scan_results.get('hosts_scanned', 0)} servers tested."
        else:
            # Fallback to default message for compatibility with existing mocks/tests
            details_text = (
                "Scan completed successfully! The database has been updated with new findings.\n\n"
                f"Discovered {self.scan_results.get('accessible_hosts', 0)} servers with accessible "
                f"{self._access_phrase()} out of {self.scan_results.get('hosts_scanned', 0)} servers tested."
            )

        if self.scan_results.get('shares_found', 0) > 0:
            share_term = "directories" if self._is_ftp_scan() else "shares"
            details_text += f"\n\nFound {self.scan_results.get('shares_found', 0)} total {share_term} "
            details_text += "available for further analysis."

        details_label = self.theme.create_styled_label(
            parent,
            details_text,
            "body",
            justify="left",
            wraplength=500
        )
        details_label.pack(anchor="w", padx=15, pady=(0, 15))
    
    def _create_interruption_details(self, parent: tk.Widget) -> None:
        """Create details for interrupted scan."""
        # Determine interruption phase
        last_phase = "unknown"
        if "last_progress_update" in self.scan_results:
            last_phase = self.scan_results["last_progress_update"].get("phase", "unknown")
        
        details_text = (
            f"Scan was interrupted during the {last_phase} phase. "
            f"However, partial results have been saved to the database.\n\n"
            f"Progress at interruption:\n"
            f"• Hosts discovered: {self.scan_results.get('hosts_scanned', 0)}\n"
            f"• Accessible hosts found: {self.scan_results.get('accessible_hosts', 0)}\n"
            f"• Duration before interruption: {self._format_duration()}\n\n"
            "You can restart the scan or view the partial results that were collected."
        )
        
        details_label = self.theme.create_styled_label(
            parent,
            details_text,
            "body",
            justify="left",
            wraplength=500
        )
        details_label.pack(anchor="w", padx=15, pady=(0, 15))
    
    def _create_error_details(self, parent: tk.Widget) -> None:
        """Create details for failed scan."""
        error_msg = self.scan_results.get("error", "Unknown error occurred")
        error_type = self.scan_results.get("error_type", "Error")
        
        details_text = (
            f"Scan failed with the following error:\n\n"
            f"Error Type: {error_type}\n"
            f"Details: {error_msg}\n\n"
        )
        
        # Add backend availability check
        if "backend" in error_msg.lower() or "not found" in error_msg.lower():
            details_text += (
                "This error suggests the Dirracuda backend may not be available or properly installed. "
                "Please ensure you have the latest version of the backend toolkit."
            )
            
            # Add GitHub link
            link_frame = tk.Frame(parent)
            self.theme.apply_to_widget(link_frame, "card")
            link_frame.pack(fill=tk.X, padx=15, pady=10)
            
            link_label = self.theme.create_styled_label(
                link_frame,
                "Get the latest Dirracuda backend:",
                "body"
            )
            link_label.pack(anchor="w")
            
            github_button = tk.Button(
                link_frame,
                text="🔗 Visit GitHub Repository",
                command=lambda: webbrowser.open("https://github.com/b3p3k0/dirracuda"),
                relief="flat",
                borderwidth=0
            )
            self.theme.apply_to_widget(github_button, "button_secondary")
            github_button.pack(anchor="w", pady=(5, 0))
        
        details_label = self.theme.create_styled_label(
            parent,
            details_text,
            "body",
            justify="left",
            wraplength=500
        )
        details_label.pack(anchor="w", padx=15, pady=(0, 15))
    
    def _create_unknown_details(self, parent: tk.Widget) -> None:
        """Create details for unknown status."""
        details_text = (
            "Scan completed with unknown status. "
            "Please check the scan results manually or try running the scan again."
        )
        
        details_label = self.theme.create_styled_label(
            parent,
            details_text,
            "body",
            justify="left",
            wraplength=500
        )
        details_label.pack(anchor="w", padx=15, pady=(0, 15))
    
    def _create_button_panel(self) -> None:
        """Create dialog button panel with context-appropriate options."""
        button_frame = tk.Frame(self.dialog)
        self.theme.apply_to_widget(button_frame, "main_window")
        button_frame.pack(fill=tk.X, padx=20, pady=(10, 20))
        
        # Close button (always present)
        self.close_button = tk.Button(
            button_frame,
            text="Close",
            command=self._close_dialog,
            relief="flat",
            borderwidth=0
        )
        self.theme.apply_to_widget(self.close_button, "button_secondary")
        self.close_button.pack(side=tk.RIGHT, padx=(10, 0))
        
    def _setup_event_handlers(self) -> None:
        """Setup event handlers."""
        self.dialog.protocol("WM_DELETE_WINDOW", self._close_dialog)
        
        # Keyboard shortcuts
        self.dialog.bind("<Return>", lambda e: self._close_dialog())
        self.dialog.bind("<Escape>", lambda e: self._close_dialog())
    
    def _focus_close_button(self) -> None:
        """Set focus to close button."""
        if hasattr(self, 'close_button'):
            self.close_button.focus_set()
    
    def _format_duration(self) -> str:
        """Format scan duration for display."""
        duration_seconds = self.scan_results.get("duration_seconds", 0)
        
        if duration_seconds < 60:
            return f"{duration_seconds:.1f} seconds"
        elif duration_seconds < 3600:
            minutes = duration_seconds / 60
            return f"{minutes:.1f} minutes"
        else:
            hours = duration_seconds / 3600
            return f"{hours:.1f} hours"
    
    def _format_scan_time(self) -> str:
        """Format scan completion time for display."""
        end_time_str = self.scan_results.get("end_time")
        if not end_time_str:
            return "Unknown"
        
        try:
            end_time = datetime.fromisoformat(end_time_str)
            return end_time.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return "Unknown"
    
    def _close_dialog(self) -> None:
        """Close dialog."""
        self.result = "close"
        self.dialog.destroy()
    
    def show(self) -> Optional[str]:
        """Show dialog and wait for result."""
        # Wait for dialog to close
        self.parent.wait_window(self.dialog)
        return self.result


def show_scan_results_dialog(parent: tk.Widget, scan_results: Dict[str, Any],
                             ) -> Optional[str]:
    """Show scan results dialog.
    
    Args:
        parent: Parent widget
        scan_results: Dictionary containing scan results and metadata
        
    Returns:
        Dialog result ("close")
    """
    dialog = ScanResultsDialog(parent, scan_results)
    return dialog.show()
