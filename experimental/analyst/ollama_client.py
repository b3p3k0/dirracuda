"""Bounded Ollama transport for Analyst.

The client owns HTTP and cancellation only. It never reads or writes Analyst's
database, logs prompt/model text, or decides durable retry state.

N1: the endpoint is supplied per client instead of being a module constant.
D17: until N3 writes the transport policy, construction refuses any non-loopback
endpoint.  That guard lives here, the last step before the socket, so a script or
a direct database write cannot bypass it.
"""

from __future__ import annotations

import hmac
from typing import Any, Callable

from .endpoint import DEFAULT_ENDPOINT, Endpoint, require_connectable
from .ollama_contract import (
    EXPECTED_IDENTITY,
    MAX_BODY_BYTES,
    ChatRequest,
    ChatResult,
    DiscoveredModel,
    OllamaIdentity,
    OllamaStatus,
    PreflightResult,
    QUALIFIED_OLLAMA_VERSION,
    ReadChatRequest,
    TagsCheckResult,
    VersionCheckResult,
    build_discovery_request,
    list_local_models,
    valid_model_digest,
    valid_model_tag,
    validate_chat_request,
)
from .transport import (
    GLOBAL_REQUEST_SLOT,
    TIMEOUT_EXCEPTIONS,
    TRANSPORT_EXCEPTIONS,
    BoundedHttpClient,
    CallerPoll,
    CancelProbe,
    HttpIntent,
    content_type_is,
    identity_encoding,
    require_bytes,
    require_caller_poll,
    require_cancel_probe,
    resource_error,
    run_caller_poll,
    safety_status,
)
from .ollama_protocol import (
    ChatStreamParser,
    OllamaAnswerError,
    OllamaProvenanceError,
    OllamaSafetyError,
    OllamaStreamError,
    SafetyCode,
    StreamCode,
    parse_answer_json,
    parse_version_response,
)


class OllamaDiscoveryError(RuntimeError):
    """A closed, content-free failure from explicit model discovery."""

    def __init__(self, status: OllamaStatus) -> None:
        if not isinstance(status, OllamaStatus) or status is OllamaStatus.SUCCESS:
            raise ValueError("discovery failure status must be terminal")
        self.status = status
        super().__init__(status.value)


class OllamaClient(BoundedHttpClient):
    """One serial, caller-bounded client for exactly one supplied endpoint.

    The bounded-HTTP machinery lives in ``transport``; this class supplies the
    Ollama URL set, its status classification, and its NDJSON chat reader. Its
    deadlines are the frozen contract values inherited unchanged.
    """

    def __init__(
        self,
        *,
        endpoint: str | Endpoint = DEFAULT_ENDPOINT,
        **kwargs: Any,
    ) -> None:
        # D17: refuse a non-loopback endpoint before any socket work happens.
        self._endpoint = require_connectable(endpoint)
        self._urls = self._endpoint.urls()
        super().__init__(**kwargs)

    @property
    def endpoint(self) -> Endpoint:
        """Return the validated endpoint this client is bound to."""
        return self._endpoint

    def preflight(
        self,
        expected: OllamaIdentity = EXPECTED_IDENTITY,
        *,
        cancel: CancelProbe,
        poll: CallerPoll | None = None,
    ) -> PreflightResult:
        """Verify daemon version plus the exact local tag/digest without inference."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return PreflightResult(OllamaStatus.CANCELLED_UNVERIFIED)
        if type(expected) is not OllamaIdentity:
            raise TypeError("preflight identity must use the exact OllamaIdentity type")
        if not _matches_expected_identity(expected):
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        version = self.check_version(cancel=cancel, poll=poll)
        if version.status is not OllamaStatus.SUCCESS:
            return PreflightResult(version.status)
        tags = self.check_tags(expected, cancel=cancel, poll=poll)
        if tags.status is not OllamaStatus.SUCCESS:
            return PreflightResult(tags.status)
        if not hmac.compare_digest(tags.model_digest, expected.model_digest):
            return PreflightResult(OllamaStatus.IDENTITY_MISMATCH)
        return PreflightResult(
            OllamaStatus.SUCCESS,
            observed_version=version.observed_version,
            model_digest=tags.model_digest,
        )

    def check_version(
        self, *, cancel: CancelProbe, poll: CallerPoll | None = None,
    ) -> VersionCheckResult:
        """Perform exactly one separately chargeable version contact."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return VersionCheckResult(OllamaStatus.CANCELLED_UNVERIFIED)
        version_intent = HttpIntent(
            "GET", self._urls.version, None, "application/json", "version",
        )
        version, status = self._execute(version_intent, cancel, poll)
        if status is not None:
            return VersionCheckResult(status)
        try:
            observed = parse_version_response(
                require_bytes(version), QUALIFIED_OLLAMA_VERSION,
            )
        except OllamaProvenanceError:
            return VersionCheckResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaSafetyError as exc:
            return VersionCheckResult(safety_status(exc))
        return VersionCheckResult(
            OllamaStatus.SUCCESS, observed_version=observed.version,
        )

    def check_tags(
        self,
        expected: OllamaIdentity = EXPECTED_IDENTITY,
        *,
        cancel: CancelProbe,
        poll: CallerPoll | None = None,
    ) -> TagsCheckResult:
        """Perform exactly one separately chargeable tag/digest contact."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return TagsCheckResult(OllamaStatus.CANCELLED_UNVERIFIED)
        if type(expected) is not OllamaIdentity:
            raise TypeError("tags identity must use the exact OllamaIdentity type")
        if not _matches_expected_identity(expected):
            return TagsCheckResult(OllamaStatus.IDENTITY_MISMATCH)
        tags_intent = HttpIntent(
            "GET", self._urls.tags, None, "application/json", "tags",
        )
        tags, status = self._execute(tags_intent, cancel, poll)
        if status is not None:
            return TagsCheckResult(status)
        try:
            parsed_tags = list_local_models(require_bytes(tags))
        except OllamaProvenanceError:
            return TagsCheckResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaSafetyError as exc:
            return TagsCheckResult(safety_status(exc))
        observed = next(
            (model for model in parsed_tags if model.model_tag == expected.model_tag),
            None,
        )
        if observed is None:
            return TagsCheckResult(OllamaStatus.IDENTITY_MISMATCH)
        return TagsCheckResult(
            OllamaStatus.SUCCESS, model_digest=observed.model_digest,
        )

    def list_models(self) -> tuple[DiscoveredModel, ...]:
        """Perform one bounded local tags request without running inference."""
        request = build_discovery_request(self._endpoint.base_url)
        intent = HttpIntent(
            request.method, request.url, request.body, request.accept, "discovery",
        )
        body, status = self._execute(intent, lambda: False, None)
        if status is not None:
            raise OllamaDiscoveryError(status)
        try:
            return list_local_models(require_bytes(body))
        except OllamaProvenanceError:
            raise OllamaDiscoveryError(OllamaStatus.IDENTITY_MISMATCH) from None
        except OllamaSafetyError as exc:
            raise OllamaDiscoveryError(safety_status(exc)) from None
        except Exception:
            raise OllamaDiscoveryError(OllamaStatus.PROTOCOL_VIOLATION) from None

    def chat(
        self,
        request: ChatRequest,
        *,
        expected_sha256: str,
        cancel: CancelProbe,
        poll: CallerPoll | None = None,
    ) -> ChatResult:
        """Run one exact chat request and retain text only on validated success."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return ChatResult(OllamaStatus.CANCELLED_UNVERIFIED)
        try:
            validate_chat_request(request)
        except (TypeError, ValueError):
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        if (
            type(expected_sha256) is not str
            or not hmac.compare_digest(request.request_sha256, expected_sha256)
        ):
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        if request.endpoint != self._endpoint.base_url:
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        intent = HttpIntent(
            "POST", self._urls.chat, request.body,
            "application/x-ndjson", "chat", request.model_tag,
            type(request) is ReadChatRequest,
        )
        value, status = self._execute(intent, cancel, poll)
        if status is not None:
            return ChatResult(status)
        if not isinstance(value, ChatResult):
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        return value


    def _read_response(
        self, response: Any, intent: HttpIntent, cancel: CancelProbe, started: float,
    ) -> object:
        """Read an NDJSON chat stream, or a whole bounded body."""
        if intent.kind == "chat":
            return self._read_chat(
                response, cancel, started, intent.model_tag,
                read_response=intent.read_response,
            )
        return self._read_all(response, cancel, started)

    def _classify_http_status(
        self,
        response: Any,
        intent: HttpIntent,
        cancel: CancelProbe,
        started: float,
    ) -> OllamaStatus | None:
        status = getattr(response, "status_code", None)
        if type(status) is not int:
            return OllamaStatus.PROTOCOL_VIOLATION
        if status == 200:
            return None
        if status in {429, 503}:
            return OllamaStatus.RESOURCE_BUSY
        if 300 <= status <= 399:
            return OllamaStatus.PROTOCOL_VIOLATION
        if 400 <= status <= 499:
            body, read_status = self._read_all_status(response, cancel, started)
            if read_status is not None:
                return read_status
            if resource_error(body):
                return OllamaStatus.RESOURCE_BUSY
            return (
                OllamaStatus.IDENTITY_MISMATCH
                if status == 404 or intent.kind != "chat"
                else OllamaStatus.PROTOCOL_VIOLATION
            )
        if 500 <= status <= 599:
            body, read_status = self._read_all_status(response, cancel, started)
            if read_status is not None:
                return read_status
            if resource_error(body):
                return OllamaStatus.RESOURCE_BUSY
            return OllamaStatus.TRANSPORT_UNAVAILABLE
        return OllamaStatus.PROTOCOL_VIOLATION

    def _read_chat(
        self,
        response: Any,
        cancel: CancelProbe,
        started: float,
        model_tag: str | None = EXPECTED_IDENTITY.model_tag,
        *,
        read_response: bool = False,
    ) -> ChatResult:
        parser = ChatStreamParser(model_tag)
        try:
            for chunk in self._wire_chunks(response, cancel, started):
                parser.feed(
                    chunk,
                    before_frame=lambda: self._check_stream_cancel(cancel),
                )
            parsed = parser.finish()
        except OllamaSafetyError as exc:
            return ChatResult(safety_status(exc))
        except OllamaProvenanceError:
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        except OllamaStreamError as exc:
            status = (
                OllamaStatus.RESOURCE_BUSY
                if exc.code is StreamCode.RESOURCE_ERROR
                else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
            return ChatResult(status)
        except TIMEOUT_EXCEPTIONS:
            return ChatResult(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.REQUEST_TIMEOUT
            )
        except TRANSPORT_EXCEPTIONS:
            return ChatResult(
                OllamaStatus.CANCELLED_UNVERIFIED
                if cancel() else OllamaStatus.TRANSPORT_UNAVAILABLE
            )
        metrics = parsed.metrics
        if metrics.done_reason == "length":
            return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        if metrics.done_reason != "stop":
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        if not read_response:
            try:
                parse_answer_json(parsed.content)
                from .worksheet import validate_shape

                validate_shape(parsed.content)
            except OllamaSafetyError as exc:
                return ChatResult(safety_status(exc))
            except OllamaAnswerError:
                return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
            except ValueError:
                return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        return ChatResult(
            OllamaStatus.SUCCESS,
            content=parsed.content,
            metrics=metrics,
        )


def _matches_expected_identity(identity: OllamaIdentity) -> bool:
    try:
        return (
            type(identity.endpoint) is str
            and identity.endpoint == EXPECTED_IDENTITY.endpoint
            and valid_model_tag(identity.model_tag)
            and valid_model_digest(identity.model_digest)
        )
    except AttributeError:
        return False


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None or not callable(getattr(headers, "get", None)):
        return ""
    value = headers.get(name, "")
    return value if type(value) is str else ""


__all__ = [
    "CallerPoll", "CancelProbe", "OllamaClient", "OllamaDiscoveryError",
]
