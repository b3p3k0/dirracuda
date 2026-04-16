#!/usr/bin/env python3
"""
Dirracuda Demo Script

Demonstrates the key features of Dirracuda in mock mode.
This script shows the dashboard functionality and component integration.
"""

import tkinter as tk
from gui.utils import safe_messagebox as messagebox
import time
import threading
from main import SMBSeekGUI


def run_demo():
    """Run GUI demo with simulated interactions."""
    print("🚀 Starting Dirracuda GUI Demo...")
    print("📊 Dashboard will show mock security data")
    print("⏱  Demo will run for 30 seconds then auto-close")
    print("💡 You can interact with the GUI during this time")
    print()
    
    try:
        # Create GUI in mock mode
        app = SMBSeekGUI(mock_mode=True)
        
        # Auto-close timer
        def auto_close():
            time.sleep(30)
            print("\n⏰ Demo complete - closing GUI")
            try:
                app.root.quit()
            except:
                pass
        
        # Start auto-close timer
        timer_thread = threading.Thread(target=auto_close, daemon=True)
        timer_thread.start()
        
        # Show demo info
        app.root.after(1000, lambda: messagebox.showinfo(
            "Dirracuda Demo",
            "🎯 Demo Features:\n\n"
            "✓ Mission Control Dashboard\n"
            "✓ Key Security Metrics\n"
            "✓ Progress Monitoring\n"
            "✓ Mock Data Display\n"
            "✓ Cross-platform Styling\n\n"
            "💡 Click metric cards for drill-downs\n"
            "⏱  Auto-closes in 30 seconds"
        ))
        
        print("🖥  GUI launched - explore the dashboard!")
        print("💡 Click on metric cards to see drill-down placeholders")
        print("🔄 Data refreshes automatically every 5 seconds")
        
        # Run the application
        app.run()
        
    except KeyboardInterrupt:
        print("\n⏹ Demo interrupted by user")
    except Exception as e:
        print(f"❌ Demo error: {e}")
    
    print("✅ Demo complete!")


if __name__ == "__main__":
    run_demo()