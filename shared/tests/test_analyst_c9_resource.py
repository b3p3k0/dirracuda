"""Pure tests for shared-GPU backoff and pause policy."""

from __future__ import annotations

import pytest

from experimental.analyst.resource_policy import (
    MAX_CONSECUTIVE_RESOURCE_FAILURES,
    RESOURCE_BACKOFF_SECONDS,
    ResourceAction,
    ResourceDecision,
    ResourcePolicyError,
    ResourceSignal,
    resource_decision,
)


def test_resource_policy_constants_and_vocabularies_are_exact() -> None:
    assert RESOURCE_BACKOFF_SECONDS == (15, 30, 60, 120, 240, 300)
    assert MAX_CONSECUTIVE_RESOURCE_FAILURES == 6
    assert {item.value for item in ResourceSignal} == {
        "recovered", "resource_busy",
    }
    assert {item.value for item in ResourceAction} == {
        "continue", "retry_wait", "paused_resource",
    }


def test_resource_failures_follow_exact_bounded_schedule_then_pause() -> None:
    prior = 0
    observed: list[tuple[ResourceAction, int, int]] = []
    for _ in RESOURCE_BACKOFF_SECONDS:
        decision = resource_decision(prior, ResourceSignal.RESOURCE_BUSY)
        observed.append(
            (decision.action, decision.consecutive_failures, decision.delay_seconds)
        )
        prior = decision.consecutive_failures

    assert observed == [
        (ResourceAction.RETRY_WAIT, 1, 15),
        (ResourceAction.RETRY_WAIT, 2, 30),
        (ResourceAction.RETRY_WAIT, 3, 60),
        (ResourceAction.RETRY_WAIT, 4, 120),
        (ResourceAction.RETRY_WAIT, 5, 240),
        (ResourceAction.PAUSED_RESOURCE, 6, 300),
    ]


def test_resource_pause_is_saturating_and_never_implies_quality_failure() -> None:
    decision = resource_decision(6, ResourceSignal.RESOURCE_BUSY)
    assert decision == ResourceDecision(ResourceAction.PAUSED_RESOURCE, 6, 300)
    assert "quality" not in decision.action.value


@pytest.mark.parametrize("prior", range(7))
def test_recovery_resets_every_legal_counter(prior: int) -> None:
    assert resource_decision(prior, ResourceSignal.RECOVERED) == ResourceDecision(
        ResourceAction.CONTINUE, 0, 0,
    )


@pytest.mark.parametrize("prior", [-1, 7, True, 1.0, "1", None])
def test_resource_policy_rejects_counter_outside_exact_integer_range(
    prior: object,
) -> None:
    with pytest.raises(ResourcePolicyError, match="consecutive"):
        resource_decision(prior, ResourceSignal.RESOURCE_BUSY)  # type: ignore[arg-type]


@pytest.mark.parametrize("signal", ["resource_busy", None, True])
def test_resource_policy_rejects_untyped_signal(signal: object) -> None:
    with pytest.raises(ResourcePolicyError, match="signal"):
        resource_decision(0, signal)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "args",
    [
        ("retry_wait", 1, 15),
        (ResourceAction.CONTINUE, 1, 0),
        (ResourceAction.RETRY_WAIT, 0, 15),
        (ResourceAction.RETRY_WAIT, 1, 14),
        (ResourceAction.RETRY_WAIT, 6, 300),
        (ResourceAction.PAUSED_RESOURCE, 5, 300),
        (ResourceAction.PAUSED_RESOURCE, 6, 299),
        (ResourceAction.CONTINUE, 0, True),
    ],
)
def test_forged_resource_decisions_are_rejected(args: tuple[object, ...]) -> None:
    with pytest.raises(ResourcePolicyError):
        ResourceDecision(*args)  # type: ignore[arg-type]
