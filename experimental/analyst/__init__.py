"""Optional offline Analyst feature.

The package root intentionally imports only standard-library modules.  Modules
with optional dependencies, such as :mod:`experimental.analyst.worksheet`, must
be imported explicitly by the Analyst worker.
"""

from .models import ANALYST_DEFAULTS, AnalystDefaults

__all__ = ["ANALYST_DEFAULTS", "AnalystDefaults"]
__version__ = "0.1.0"
