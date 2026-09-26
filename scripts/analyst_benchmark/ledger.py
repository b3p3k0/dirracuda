"""
Hard call ledger and soft wall-clock pause.

DISPOSITION: retained diagnostic.

Call counts and output-size caps are HARD safety limits: exceeding one stops the
stage. Wall-clock budgets are SOFT pause thresholds: crossing one yields the
resumable state PAUSED_RESOURCE, never BLOCKED or INCONCLUSIVE. Waiting for a
shared GPU must not turn a valid experiment into a failure.

The ledger counts EVERY request that reaches Ollama - scored calls, warm-ups,
/api/show probes, the top_k probe, preflight requests, retries, and cancellation
probes - not just scored requests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

STATE_RUNNING = "running"
STATE_PAUSED_RESOURCE = "PAUSED_RESOURCE"
STATE_BLOCKED = "BLOCKED"
STATE_COMPLETE = "stage_b_complete"


class HardCapExceeded(RuntimeError):
    """A hard safety limit was reached. The stage stops."""


@dataclass
class Ledger:
    hard_cap: int
    soft_wall_seconds: float
    started_monotonic: float = field(default_factory=time.monotonic)
    counts: Dict[str, int] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)
    state: str = STATE_RUNNING

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def remaining(self) -> int:
        return max(self.hard_cap - self.total, 0)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_monotonic

    def charge(self, kind: str, n: int = 1) -> None:
        """Record n requests of `kind`. Raises once the hard cap is reached."""
        if self.total + n > self.hard_cap:
            self.state = STATE_BLOCKED
            raise HardCapExceeded(
                f"call ledger hard cap {self.hard_cap} reached "
                f"(charging {n}x {kind}, already {self.total}). "
                f"Unblock: raise --call-cap or reduce the design, then resume "
                f"from the last checkpoint.")
        self.counts[kind] = self.counts.get(kind, 0) + n

    def soft_wall_crossed(self) -> bool:
        return self.elapsed > self.soft_wall_seconds

    def pause(self, reason: str) -> None:
        self.state = STATE_PAUSED_RESOURCE
        self.events.append(f"paused: {reason}")

    def note(self, msg: str) -> None:
        self.events.append(msg)

    def summary(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "calls_total": self.total,
            "calls_by_kind": dict(sorted(self.counts.items())),
            "hard_cap": self.hard_cap,
            "elapsed_seconds": round(self.elapsed, 1),
            "soft_wall_seconds": self.soft_wall_seconds,
            "soft_wall_crossed": self.soft_wall_crossed(),
            "events": list(self.events),
        }
