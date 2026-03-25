"""Logging configuration for Dirracuda.

Provides a centralized logging setup with:
- Default WARNING level (silent under normal operation)
- Debug via XSMBSEEK_DEBUG_* environment variables
- Stderr output only (no file handlers)
- Safe for library imports (NullHandler fallback)
"""

import logging
import os
import sys

# Named logger for GUI subsystem
GUI_LOGGER_NAME = "dirracuda_gui"


def setup_gui_logging() -> logging.Logger:
    """Configure GUI logging. Call once at startup.

    Safe to call multiple times (idempotent).
    Returns the root GUI logger.
    """
    logger = logging.getLogger(GUI_LOGGER_NAME)

    # Avoid duplicate handlers on re-entry/tests
    if logger.handlers:
        return logger

    # Default: WARNING level (silent under normal operation)
    level = logging.WARNING

    # Honor existing env vars for debug
    if (os.getenv("XSMBSEEK_DEBUG_SUBPROCESS") or os.getenv("DIRRACUDA_DEBUG_SUBPROCESS")
            or os.getenv("XSMBSEEK_DEBUG_PARSING") or os.getenv("DIRRACUDA_DEBUG_PARSING")):
        level = logging.DEBUG

    logger.setLevel(level)

    # Stream to stderr (not stdout)
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("impacket").setLevel(logging.WARNING)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the GUI namespace.

    If setup_gui_logging() hasn't been called, returns a logger
    with NullHandler (safe for library imports).

    Args:
        name: Module name (typically __name__ or a descriptive string)

    Returns:
        A logger instance under the dirracuda_gui namespace
    """
    logger = logging.getLogger(f"{GUI_LOGGER_NAME}.{name}")
    if not logger.handlers and not logging.getLogger(GUI_LOGGER_NAME).handlers:
        logger.addHandler(logging.NullHandler())
    return logger
