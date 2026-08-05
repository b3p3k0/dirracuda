"""Entry point: python -m scripts.analyst_benchmark"""
from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(main())
