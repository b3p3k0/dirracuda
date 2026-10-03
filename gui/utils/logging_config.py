"""Logging configuration for Dirracuda.

Provides a centralized logging setup with:
- Default WARNING level (silent under normal operation)
- Debug via XSMBSEEK_DEBUG_* environment variables
- Brief stderr messages; exception diagnostics saved under /tmp
- Safe for library imports (NullHandler fallback)
"""

import copy
import logging
import os
import shlex
import sys
import tempfile

# Named logger for GUI subsystem
GUI_LOGGER_NAME = "dirracuda_gui"


class ConsoleFormatter(logging.Formatter):
    """Keep diagnostics in private dumps and console messages brief."""

    def format(self, record):
        # Formatter caches exception text on records. Keep the original intact
        # for other handlers, including diagnostic capture in tests.
        text = super().format(copy.copy(record))
        if record.exc_info or record.exc_text or record.stack_info or "\n" in text:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", errors="backslashreplace",
                    prefix="dirracuda-", suffix=".log", dir="/tmp", delete=False,
                ) as dump:
                    dump.write(text + "\n")
                    dump_path = dump.name
            except OSError as exc:
                # Never discard diagnostics if the disk is full/unavailable.
                return self._colorize(text, record.levelno) + (
                    f"\nCould not save diagnostic dump: {exc}"
                )
            summary = text.splitlines()[0]
            if summary.endswith("; details follow"):
                summary = summary[:-len("; details follow")]
            return self._colorize(summary, record.levelno) + (
                f" Console log is saved; run cat {shlex.quote(dump_path)} to view."
            )
        return self._colorize(text, record.levelno)

    def _colorize(self, text, level):
        if (sys.stderr.isatty() and "NO_COLOR" not in os.environ
                and os.environ.get("TERM") != "dumb"):
            if level >= logging.ERROR:
                return f"\033[31m{text}\033[0m"
            if level >= logging.WARNING:
                return f"\033[33m{text}\033[0m"
        return text


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
    handler.setFormatter(ConsoleFormatter(
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
