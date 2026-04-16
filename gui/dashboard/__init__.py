# Load the shim first so _mb()/_d() in widget.py, dashboard_scan.py,
# and dashboard_batch_ops.py can resolve gui.components.dashboard via sys.modules.
# Idempotent: no-op if the shim is already in sys.modules.
import gui.components.dashboard  # noqa: F401
from gui.dashboard.widget import DashboardWidget

__all__ = ["DashboardWidget"]
