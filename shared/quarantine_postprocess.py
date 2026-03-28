from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class PostProcessInput:
    file_path: Path     # absolute path of the downloaded file
    ip_address: str
    share: str
    rel_display: str    # e.g. "subdir/file.txt"
    file_size: int


@dataclass
class PostProcessResult:
    final_path: Path        # where file ended up (same as file_path when not moved)
    verdict: str            # "skipped" | "clean" | "infected" | "error"
    moved: bool             # True if file was relocated
    destination: str        # "quarantine" | "extracted" | "known_bad"
    metadata: Optional[Any] # caller-defined detail (e.g. ScanResult in C3+); None in C2
    error: Optional[str]    # set when verdict == "error", or when a move failed for "clean"/"infected" verdicts


# Callable contract type alias
PostProcessorFn = Callable[[PostProcessInput], PostProcessResult]


def passthrough_processor(inp: PostProcessInput) -> PostProcessResult:
    """No-op. Used when ClamAV is disabled. File stays where it is."""
    return PostProcessResult(
        final_path=inp.file_path,
        verdict="skipped",
        moved=False,
        destination="quarantine",
        metadata=None,
        error=None,
    )


__all__ = [
    "PostProcessInput",
    "PostProcessResult",
    "PostProcessorFn",
    "passthrough_processor",
]
