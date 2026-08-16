"""Pure shared-resource backoff policy for Analyst model dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


RESOURCE_BACKOFF_SECONDS: Final = (15, 30, 60, 120, 240, 300)
MAX_CONSECUTIVE_RESOURCE_FAILURES: Final = len(RESOURCE_BACKOFF_SECONDS)


class ResourcePolicyError(ValueError):
    """Resource state is outside the frozen C9 policy."""


class ResourceSignal(str, Enum):
    """Trusted input to the policy after transport classification."""

    RECOVERED = "recovered"
    RESOURCE_BUSY = "resource_busy"


class ResourceAction(str, Enum):
    """The next scheduler action; no action implies model quality."""

    CONTINUE = "continue"
    RETRY_WAIT = "retry_wait"
    PAUSED_RESOURCE = "paused_resource"


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    """One deterministic transition of the consecutive-failure counter."""

    action: ResourceAction
    consecutive_failures: int
    delay_seconds: int

    def __post_init__(self) -> None:
        if not isinstance(self.action, ResourceAction):
            raise ResourcePolicyError("resource action is not closed")
        if (
            type(self.consecutive_failures) is not int
            or not 0 <= self.consecutive_failures <= MAX_CONSECUTIVE_RESOURCE_FAILURES
            or type(self.delay_seconds) is not int
            or self.delay_seconds < 0
        ):
            raise ResourcePolicyError("resource decision counters are invalid")
        if self.action is ResourceAction.CONTINUE:
            valid = self.consecutive_failures == 0 and self.delay_seconds == 0
        elif self.action is ResourceAction.RETRY_WAIT:
            valid = (
                1 <= self.consecutive_failures < MAX_CONSECUTIVE_RESOURCE_FAILURES
                and self.delay_seconds
                == RESOURCE_BACKOFF_SECONDS[self.consecutive_failures - 1]
            )
        else:
            valid = (
                self.consecutive_failures == MAX_CONSECUTIVE_RESOURCE_FAILURES
                and self.delay_seconds == RESOURCE_BACKOFF_SECONDS[-1]
            )
        if not valid:
            raise ResourcePolicyError("resource decision contradicts the frozen policy")


def resource_decision(
    consecutive_failures: int,
    signal: ResourceSignal,
) -> ResourceDecision:
    """Advance or reset the six-step resource policy.

    ``consecutive_failures`` is the durable count before the current trusted
    signal.  Failures 1–5 wait in-process; failure 6 records the 300-second
    cooldown and pauses for an explicit later resume.  A successful bounded
    contact resets the counter, including after a pause.
    """
    if (
        type(consecutive_failures) is not int
        or not 0 <= consecutive_failures <= MAX_CONSECUTIVE_RESOURCE_FAILURES
    ):
        raise ResourcePolicyError("consecutive resource failures are invalid")
    if not isinstance(signal, ResourceSignal):
        raise ResourcePolicyError("resource signal is not closed")
    if signal is ResourceSignal.RECOVERED:
        return ResourceDecision(ResourceAction.CONTINUE, 0, 0)

    advanced = min(
        consecutive_failures + 1,
        MAX_CONSECUTIVE_RESOURCE_FAILURES,
    )
    action = (
        ResourceAction.PAUSED_RESOURCE
        if advanced == MAX_CONSECUTIVE_RESOURCE_FAILURES
        else ResourceAction.RETRY_WAIT
    )
    return ResourceDecision(
        action,
        advanced,
        RESOURCE_BACKOFF_SECONDS[advanced - 1],
    )


__all__ = [
    "MAX_CONSECUTIVE_RESOURCE_FAILURES",
    "RESOURCE_BACKOFF_SECONDS",
    "ResourceAction",
    "ResourceDecision",
    "ResourcePolicyError",
    "ResourceSignal",
    "resource_decision",
]
