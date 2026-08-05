"""
Hash-pin for the frozen C0B-1 benchmark protocol.

DISPOSITION: retained diagnostic.

The protocol document is pre-registered: it states the decision rule, the gates,
the factors, and the budgets BEFORE any live call. If the file changes after a
run record has pinned it, the run is no longer measuring what it declared, so
scoring refuses to proceed.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = (REPO_ROOT / "docs" / "dev" / "ollama_integration"
                 / "BENCHMARK_PROTOCOL_C0B1.md")
PROTOCOL_VERSION = "c0b1-protocol-v1"
# Independently frozen before the Stage-B run. Production callers compare the
# document against this value; they never bless whatever bytes happen to exist.
FROZEN_PROTOCOL_SHA256 = "5ab9e56d628c6d7449ae54956ab35b68b3bd920329700a2aaef1749044767199"


class ProtocolPin(NamedTuple):
    version: str
    path: str
    sha256: str


class ProtocolMismatch(RuntimeError):
    """The protocol document changed after the run pinned it."""


def compute_sha256(path: Path | None = None) -> str:
    p = path or PROTOCOL_PATH
    return hashlib.sha256(p.read_bytes()).hexdigest()


def pin(path: Path | None = None, *,
        expected_sha256: str | None = None) -> ProtocolPin:
    """Verify an independently supplied identity and return the durable pin.

    The default production document uses the frozen constant above. Tests and
    tools passing another path may explicitly supply that artifact's expected
    hash; the legacy custom-path behavior remains available for pure unit tests.
    """
    p = path or PROTOCOL_PATH
    expected = expected_sha256
    if expected is None:
        expected = FROZEN_PROTOCOL_SHA256 if path is None else compute_sha256(p)
    actual = compute_sha256(p)
    if actual != expected:
        raise ProtocolMismatch(
            f"protocol does not match independently frozen identity: "
            f"expected {expected[:16]}..., found {actual[:16]}...")
    try:
        label = str(p.relative_to(REPO_ROOT))
    except ValueError:
        label = str(p)          # a protocol held outside the repo still pins
    return ProtocolPin(PROTOCOL_VERSION, label, expected)


def verify(expected: ProtocolPin, path: Path | None = None) -> None:
    """Raise unless the protocol still matches what the run recorded."""
    actual = compute_sha256(path)
    if actual != expected.sha256:
        raise ProtocolMismatch(
            f"protocol {expected.path} changed after the run pinned it: "
            f"recorded {expected.sha256[:16]}..., found {actual[:16]}.... "
            "Scoring refuses to proceed against an altered pre-registration."
        )
