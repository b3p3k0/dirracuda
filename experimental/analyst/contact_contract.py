"""Pure contracts for durable Analyst Ollama contact scheduling."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .endpoint import ollama_urls
from .ollama_contract import (
    OLLAMA_ENDPOINT,
    OLLAMA_PS_URL,
    OLLAMA_TAGS_URL,
    OLLAMA_VERSION_URL,
)
from .resource_policy import (
    MAX_CONSECUTIVE_RESOURCE_FAILURES,
    RESOURCE_BACKOFF_SECONDS,
)
from .state import AttemptState


MAX_CONTROL_CONTACTS_PER_RUN: Final = 64
MAX_CHAT_CONTACTS_PER_CHUNK: Final = 16

_LOWER_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class ContactContractError(ValueError):
    """A contact or schedule value contradicts the frozen C9B contract."""


class ContactKind(str, Enum):
    VERSION = "version"
    TAGS = "tags"
    PS = "ps"
    CHAT = "chat"
    CANCELLATION_HEALTH = "cancellation_health"


class ContactStatus(str, Enum):
    DISPATCHING = "dispatching"
    SUCCESS = "success"
    MODEL_INVALID = "model_invalid"
    CANCELLED_UNVERIFIED = "cancelled_unverified"
    REQUEST_TIMEOUT = "request_timeout"
    RESOURCE_BUSY = "resource_busy"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    PROTOCOL_VIOLATION = "protocol_violation"
    RESPONSE_LIMIT = "response_limit"
    IDENTITY_MISMATCH = "identity_mismatch"
    ORPHANED_UNKNOWN = "orphaned_unknown"
    # Contract section 4: both must stay distinguishable from model_invalid and
    # from a generic transport failure, on the durable record as well as in the
    # returned result. Decision D20.
    #: The server refused the prompt as larger than its context.
    CONTEXT_EXCEEDED = "context_exceeded"
    #: The server answered, but Analyst had asked for something unusable -
    #: empty content beside a populated reasoning channel, for instance.
    CONFIGURATION_FAILURE = "configuration_failure"


class ScheduleState(str, Enum):
    AVAILABLE = "available"
    BACKOFF = "backoff"
    PAUSED_RESOURCE = "paused_resource"


def _control_request_sha256(kind: ContactKind, url: str) -> str:
    body = json.dumps(
        {
            "accept": "application/json",
            "accept_encoding": "identity",
            "kind": kind.value,
            "method": "GET",
            "url": url,
            "version": 1,
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(body).hexdigest()


_CONTROL_URL_FIELD: Final = {
    ContactKind.VERSION: "version",
    ContactKind.TAGS: "tags",
    ContactKind.PS: "ps",
}


def control_request_sha256(
    kind: ContactKind, endpoint: str = OLLAMA_ENDPOINT,
) -> str:
    """Return one control-contact request seal for a supplied endpoint.

    N1: the URL is part of the request shape, so each control seal is a function
    of the endpoint rather than a module-level constant.  The three constants
    below keep the pre-N1 loopback values.
    """
    field = _CONTROL_URL_FIELD.get(kind)
    if field is None:
        raise ContactContractError("kind has no control URL")
    return _control_request_sha256(kind, getattr(ollama_urls(endpoint), field))


def version_request_sha256(endpoint: str = OLLAMA_ENDPOINT) -> str:
    """Return the ``/api/version`` contact seal for one endpoint."""
    return control_request_sha256(ContactKind.VERSION, endpoint)


def tags_request_sha256(endpoint: str = OLLAMA_ENDPOINT) -> str:
    """Return the ``/api/tags`` contact seal for one endpoint."""
    return control_request_sha256(ContactKind.TAGS, endpoint)


def ps_request_sha256(endpoint: str = OLLAMA_ENDPOINT) -> str:
    """Return the ``/api/ps`` contact seal for one endpoint."""
    return control_request_sha256(ContactKind.PS, endpoint)


#: The loopback control seals, unchanged from the pre-N1 build.
VERSION_REQUEST_SHA256: Final = _control_request_sha256(
    ContactKind.VERSION, OLLAMA_VERSION_URL,
)
TAGS_REQUEST_SHA256: Final = _control_request_sha256(
    ContactKind.TAGS, OLLAMA_TAGS_URL,
)
PS_REQUEST_SHA256: Final = _control_request_sha256(
    ContactKind.PS, OLLAMA_PS_URL,
)


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    state: ScheduleState
    consecutive_failures: int
    delay_seconds: int
    not_before_utc: str | None
    resume_authorized_at_utc: str | None
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.state, ScheduleState):
            raise ContactContractError("schedule state is not closed")
        if (
            type(self.consecutive_failures) is not int
            or not 0 <= self.consecutive_failures <= MAX_CONSECUTIVE_RESOURCE_FAILURES
            or type(self.delay_seconds) is not int
            or self.delay_seconds < 0
            or type(self.revision) is not int
            or self.revision < 0
            or not _optional_timestamp(self.not_before_utc)
            or not _optional_timestamp(self.resume_authorized_at_utc)
        ):
            raise ContactContractError("schedule counters or timestamps are invalid")
        if self.state is ScheduleState.AVAILABLE:
            valid = (
                self.consecutive_failures == 0
                and self.delay_seconds == 0
                and self.not_before_utc is None
                and self.resume_authorized_at_utc is None
            )
        elif self.state is ScheduleState.BACKOFF:
            valid = (
                1 <= self.consecutive_failures < MAX_CONSECUTIVE_RESOURCE_FAILURES
                and self.delay_seconds
                == RESOURCE_BACKOFF_SECONDS[self.consecutive_failures - 1]
                and self.not_before_utc is not None
                and self.resume_authorized_at_utc is None
            )
        else:
            valid = (
                self.consecutive_failures == MAX_CONSECUTIVE_RESOURCE_FAILURES
                and self.delay_seconds == RESOURCE_BACKOFF_SECONDS[-1]
                and self.not_before_utc is not None
            )
        if not valid:
            raise ContactContractError("schedule fields contradict its state")


@dataclass(frozen=True, slots=True)
class ContactCharge:
    contact_id: str
    run_id: str
    contact_no: int
    kind: ContactKind
    chunk_id: int | None
    semantic_attempt_no: int | None
    request_sha256: str
    lease_generation: int
    resource_failures_before: int

    def __post_init__(self) -> None:
        if (
            not _sha(self.contact_id)
            or type(self.run_id) is not str
            or not self.run_id
            or type(self.contact_no) is not int
            or self.contact_no <= 0
            or not isinstance(self.kind, ContactKind)
            or not _sha(self.request_sha256)
            or type(self.lease_generation) is not int
            or self.lease_generation <= 0
            or type(self.resource_failures_before) is not int
            or not 0 <= self.resource_failures_before <= 6
        ):
            raise ContactContractError("contact charge identity is invalid")
        if self.kind is ContactKind.CHAT:
            valid_owner = (
                type(self.chunk_id) is int
                and self.chunk_id > 0
                and type(self.semantic_attempt_no) is int
                and self.semantic_attempt_no in {1, 2}
            )
        else:
            valid_owner = self.chunk_id is None and self.semantic_attempt_no is None
        if not valid_owner:
            raise ContactContractError("contact kind contradicts semantic ownership")


@dataclass(frozen=True, slots=True)
class ContactFinish:
    contact_id: str
    kind: ContactKind
    status: ContactStatus
    semantic_attempt_no: int | None
    attempt_id: str | None
    schedule: ScheduleSnapshot
    lease_released: bool

    def __post_init__(self) -> None:
        if (
            not _sha(self.contact_id)
            or not isinstance(self.kind, ContactKind)
            or not isinstance(self.status, ContactStatus)
            or self.status is ContactStatus.DISPATCHING
            or not isinstance(self.schedule, ScheduleSnapshot)
            or type(self.lease_released) is not bool
        ):
            raise ContactContractError("contact finish identity is invalid")
        if self.kind is ContactKind.CHAT:
            valid_attempt = self.semantic_attempt_no in {1, 2} and (
                self.attempt_id is None
                if self.status is ContactStatus.RESOURCE_BUSY
                else _sha(self.attempt_id)
            )
        else:
            valid_attempt = self.semantic_attempt_no is None and self.attempt_id is None
        if not valid_attempt:
            raise ContactContractError("contact finish contradicts semantic ownership")
        if (
            self.status is ContactStatus.MODEL_INVALID
            and self.kind not in {ContactKind.CHAT, ContactKind.CANCELLATION_HEALTH}
        ):
            raise ContactContractError("control contact cannot report model validation")
        if self.lease_released != (
            self.status is ContactStatus.RESOURCE_BUSY
            and self.schedule.state is ScheduleState.PAUSED_RESOURCE
        ):
            raise ContactContractError("contact finish contradicts lease ownership")


def semantic_attempt_state(status: ContactStatus) -> AttemptState | None:
    """Map a terminal chat contact to its durable two-attempt outcome."""
    if not isinstance(status, ContactStatus):
        raise ContactContractError("contact status is not closed")
    mapping = {
        ContactStatus.SUCCESS: AttemptState.DISPATCHING,
        ContactStatus.MODEL_INVALID: AttemptState.SCHEMA_INVALID,
        ContactStatus.REQUEST_TIMEOUT: AttemptState.MODEL_TIMEOUT,
        ContactStatus.TRANSPORT_UNAVAILABLE: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.PROTOCOL_VIOLATION: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.RESPONSE_LIMIT: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.IDENTITY_MISMATCH: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.CANCELLED_UNVERIFIED: AttemptState.CANCELLED_UNVERIFIED,
        ContactStatus.ORPHANED_UNKNOWN: AttemptState.ORPHANED_UNKNOWN,
        # Contract section 4 requires both to be distinguishable from
        # model_invalid, which is SCHEMA_INVALID here. Neither is the model's
        # fault, so neither may map to it. MODEL_TRANSPORT_ERROR consumes a
        # semantic attempt and exhausts after two, which is right: both are
        # deterministic for a given prompt, so retrying forever would loop.
        # The durable contact row carries the precise reason.
        ContactStatus.CONTEXT_EXCEEDED: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.CONFIGURATION_FAILURE: AttemptState.MODEL_TRANSPORT_ERROR,
        ContactStatus.RESOURCE_BUSY: None,
    }
    if status is ContactStatus.DISPATCHING:
        raise ContactContractError("dispatching contact has no terminal attempt state")
    return mapping[status]


def resets_resource_streak(kind: ContactKind, status: ContactStatus) -> bool:
    """Return whether an answered generation proves the resource recovered."""
    if not isinstance(kind, ContactKind) or not isinstance(status, ContactStatus):
        raise ContactContractError("contact recovery inputs are not closed")
    return kind in {ContactKind.CHAT, ContactKind.CANCELLATION_HEALTH} and status in {
        ContactStatus.SUCCESS,
        ContactStatus.MODEL_INVALID,
    }


def _sha(value: object) -> bool:
    return type(value) is str and _LOWER_SHA.fullmatch(value) is not None


def _optional_timestamp(value: object) -> bool:
    return value is None or (type(value) is str and 1 <= len(value) <= 40)


__all__ = [
    "ContactCharge",
    "ContactContractError",
    "ContactFinish",
    "ContactKind",
    "ContactStatus",
    "MAX_CHAT_CONTACTS_PER_CHUNK",
    "MAX_CONTROL_CONTACTS_PER_RUN",
    "PS_REQUEST_SHA256",
    "ScheduleSnapshot",
    "ScheduleState",
    "TAGS_REQUEST_SHA256",
    "VERSION_REQUEST_SHA256",
    "resets_resource_streak",
    "semantic_attempt_state",
]
