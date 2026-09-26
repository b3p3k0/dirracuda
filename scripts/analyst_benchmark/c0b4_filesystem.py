"""Exact-mode filesystem revalidation for C0B-4.

C0B-4 creation probes only its frozen journal mode, so invocation revalidation must
hash that same one-mode capability body.  The inherited C0B-2 helper intentionally
retains its older two-mode behavior.

DISPOSITION: benchmark-only; remove after the accepted confirmation is handed to C1.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .c0b2_checkpoint import CheckpointError
from .c0b2_fsprobe import FilesystemProbe, MountFingerprint, probe_filesystem


def revalidate_frozen_filesystem(
        expected: MountFingerprint, path: Path, selected_mode: str,
        capability_sha256: str, *,
        probe: Callable[..., FilesystemProbe] = probe_filesystem,
) -> FilesystemProbe:
    """Recheck the exact capability preimage used when the C0B-4 run was created."""
    if selected_mode not in {"DELETE", "WAL"}:
        raise CheckpointError("invalid frozen C0B-4 journal mode")
    result = probe(path, modes=(selected_mode,))
    if (result.fingerprint.sha256 != expected.sha256
            or result.capability_sha256 != capability_sha256
            or result.selected_mode != selected_mode):
        raise CheckpointError("filesystem capability changed since run creation")
    return result
