"""Helpers for loading the canonical ./dirracuda entrypoint module."""

from __future__ import annotations

from functools import lru_cache
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Type


@lru_cache(maxsize=1)
def load_dirracuda_module() -> ModuleType:
    """Load and cache the top-level ``dirracuda`` script as a Python module."""
    script_path = Path(__file__).resolve().parents[2] / "dirracuda"
    loader = SourceFileLoader("dirracuda_entrypoint_module", str(script_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load dirracuda module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_canonical_gui_class() -> Type[object]:
    """Return the canonical GUI application class from ``./dirracuda``."""
    module = load_dirracuda_module()
    gui_cls = getattr(module, "XSMBSeekGUI", None)
    if gui_cls is None:
        raise ImportError("dirracuda module does not expose XSMBSeekGUI")
    return gui_cls
