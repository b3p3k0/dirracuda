"""The OpenAI-compatible backend: llama.cpp, local and private-LAN.

Every number here traces to a measurement against `mimir`, llama.cpp build
b1-f280b26, rather than to vendor documentation.

Address policy (contract 4.1-4.3) is applied at construction and again before
each run, so no unguarded remote path exists at any point.
"""

from __future__ import annotations

from typing import Any

from ..endpoint import Endpoint, parse_endpoint
from ..ollama_contract import (
    ChatMetrics,
    ChatRequest,
    ChatResult,
    OllamaStatus,
    TagsCheckResult,
    VersionCheckResult,
    validate_chat_request,
)
from ..transport import (
    BoundedHttpClient,
    CallerPoll,
    CancelProbe,
    HttpIntent,
    content_type_is,
    identity_encoding,
    require_bytes,
    require_caller_poll,
    require_cancel_probe,
    require_permitted,
    run_caller_poll,
)
from . import openai_protocol as protocol
from .base import (
    UNKNOWN_CONTEXT,
    BackendCapabilities,
    BackendError,
    BackendKind,
    IdentityKind,
    ModelIdentity,
)

_JSON = "application/json"
_SSE = "text/event-stream"


class OpenAICompatBackend(BoundedHttpClient):
    """One serial client for a server speaking the OpenAI dialect."""

    kind = BackendKind.OPENAI_COMPAT

    #: Contract 8. The TCP connect is fast even when a model is cold, so the
    #: connect timeout stays short. The read and total deadlines are set
    #: against measurement: 11,058 prompt tokens took 33 s on an already-warm
    #: 27B, and a router may hold a model unloaded or sleeping so the first
    #: request also pays a load. These are this backend's alone -- the frozen
    #: Ollama values are untouched (review finding M1).
    CONNECT_TIMEOUT: float = 10.0
    IDLE_READ_TIMEOUT: float = 600.0
    TOTAL_REQUEST_TIMEOUT: float = 1800.0

    def __init__(
        self,
        *,
        endpoint: str | Endpoint,
        plaintext_ack: bool = False,
        model_id: str | None = None,
        cert_fingerprint: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._endpoint = require_permitted(endpoint, plaintext_ack=plaintext_ack)
        kwargs["cert_fingerprint"] = cert_fingerprint
        self._plaintext_ack = plaintext_ack
        self._model_id = model_id
        self._base = self._endpoint.base_url
        super().__init__(**kwargs)

    @property
    def endpoint(self) -> Endpoint:
        """Return the endpoint this backend is bound to."""
        return self._endpoint

    def recheck_address(self) -> Endpoint:
        """Re-resolve and re-check the host (contract 4.3), before a run."""
        self._endpoint = require_permitted(
            self._endpoint, plaintext_ack=self._plaintext_ack
        )
        return self._endpoint

    # ---- control surface ------------------------------------------------

    def props(self, *, cancel: CancelProbe) -> dict[str, Any]:
        """Return `/props`, which says whether this is a router."""
        return self._json(f"{self._base}/props", "props", cancel)

    def health(self, *, cancel: CancelProbe) -> bool:
        """Return whether `/health` answers."""
        try:
            self._json(f"{self._base}/health", "health", cancel)
            return True
        except BackendError:
            return False

    def list_models(
        self, *, cancel: CancelProbe = lambda: False,
    ) -> tuple[protocol.ServerModel, ...]:
        """Return only the models that can actually serve a chat run."""
        payload = self._json(f"{self._base}/v1/models", "models", cancel)
        return protocol.text_generation_models(protocol.parse_models(payload))

    def capabilities(
        self, model_id: str, *, cancel: CancelProbe = lambda: False,
    ) -> BackendCapabilities:
        """Return what this server will honour for one model (contract 5.3)."""
        props = self.props(cancel=cancel)
        model = next(
            (m for m in self.list_models(cancel=cancel) if m.model_id == model_id),
            None,
        )
        return BackendCapabilities(
            max_context=protocol.discover_context(props, model),
            # llama.cpp fixes context at launch; it is not per request.
            can_set_context_per_request=False,
            supports_json_schema=True,
            supports_seed=True,
            server_version=protocol.server_build(props),
        )

    def identity_for(
        self, model_id: str, *, cancel: CancelProbe = lambda: False,
    ) -> ModelIdentity:
        """Return the reported identity this server establishes for a model.

        Never a digest: nothing here proves which weights answered, so the
        identity is labelled `reported` and must not display as verified.
        """
        props = self.props(cancel=cancel)
        model = next(
            (m for m in self.list_models(cancel=cancel) if m.model_id == model_id),
            None,
        )
        if model is None:
            raise BackendError(f"{model_id} is not a text-generation model here")
        meta = model.meta if type(model.meta) is dict else {}
        context = protocol.discover_context(props, model)
        return ModelIdentity(
            kind=IdentityKind.REPORTED,
            model_name=model.model_id,
            model_path=_text(meta.get("model_path")),
            n_params=_count(meta.get("n_params")),
            size_bytes=_count(meta.get("size")),
            ftype=_text(meta.get("ftype")),
            n_vocab=_count(meta.get("n_vocab")),
            n_ctx=context or None,
            n_ctx_train=_count(meta.get("n_ctx_train")),
            server_fingerprint=protocol.server_build(props),
        )

    # ---- the run path ---------------------------------------------------

    def chat(
        self,
        request: ChatRequest,
        *,
        expected_sha256: str,
        cancel: CancelProbe,
        poll: CallerPoll = None,
        model_id: str | None = None,
    ) -> ChatResult:
        """Run one chat request against this server."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return ChatResult(OllamaStatus.CANCELLED_UNVERIFIED)
        try:
            validate_chat_request(request)
        except (TypeError, ValueError):
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        if type(expected_sha256) is not str or request.request_sha256 != expected_sha256:
            return ChatResult(OllamaStatus.IDENTITY_MISMATCH)
        target = model_id or self._model_id or request.model_tag
        try:
            body = protocol.build_chat_body(request.payload(), model_id=target)
        except BackendError:
            return ChatResult(OllamaStatus.CONFIGURATION_FAILURE)
        intent = HttpIntent(
            "POST", f"{self._base}/v1/chat/completions",
            _encode(body), _SSE, "chat", target, False,
        )
        value, status = self._execute(intent, cancel, poll)
        if status is not None:
            return ChatResult(status)
        return self._result_from(require_bytes(value), request)

    # ---- the control surface the run engine expects ---------------------
    #
    # Erratum E19: this backend cannot answer a digest preflight, because it
    # publishes no digest. It is preflighted against what it can prove -- that
    # the server answers, and that the model exists and is a text-generation
    # model -- and its identity is recorded as "reported".

    def check_version(
        self, *, cancel: CancelProbe, poll: CallerPoll = None,
    ) -> VersionCheckResult:
        """Report the server build, which stands in for a daemon version."""
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return VersionCheckResult(OllamaStatus.CANCELLED_UNVERIFIED)
        try:
            build = protocol.server_build(self.props(cancel=cancel))
        except BackendError:
            return VersionCheckResult(OllamaStatus.TRANSPORT_UNAVAILABLE)
        if not build:
            return VersionCheckResult(OllamaStatus.IDENTITY_MISMATCH)
        return VersionCheckResult(OllamaStatus.SUCCESS, observed_version=build)

    def check_model(
        self, model_id: str, *, cancel: CancelProbe, poll: CallerPoll = None,
    ) -> OllamaStatus:
        """Verify the model exists here and can serve a chat run.

        The digest-shaped `check_tags` has no meaning for this backend, so the
        run engine calls this instead when the identity kind is "reported".
        """
        require_cancel_probe(cancel)
        require_caller_poll(poll)
        run_caller_poll(poll)
        if cancel():
            return OllamaStatus.CANCELLED_UNVERIFIED
        try:
            models = self.list_models(cancel=cancel)
        except BackendError:
            return OllamaStatus.TRANSPORT_UNAVAILABLE
        if not models:
            return OllamaStatus.IDENTITY_MISMATCH
        if not any(m.model_id == model_id for m in models):
            return OllamaStatus.IDENTITY_MISMATCH
        return OllamaStatus.SUCCESS

    def check_tags(
        self, expected: Any = None, *, cancel: CancelProbe, poll: CallerPoll = None,
    ) -> TagsCheckResult:
        """Refuse a digest check this backend can never satisfy.

        Kept so the backend presents the surface the run engine validates, and
        so calling it is a loud identity mismatch rather than an AttributeError.
        """
        require_cancel_probe(cancel)
        return TagsCheckResult(OllamaStatus.IDENTITY_MISMATCH)

    # ---- transport hooks ------------------------------------------------

    def _classify_http_status(
        self, response: Any, intent: HttpIntent, cancel: CancelProbe, started: float,
    ) -> OllamaStatus | None:
        status = getattr(response, "status_code", None)
        if type(status) is not int:
            return OllamaStatus.PROTOCOL_VIOLATION
        if status == 200:
            return None
        body, read_status = self._read_all_status(response, cancel, started)
        if read_status is not None:
            return read_status
        kind, overflow = protocol.parse_error(body)
        if overflow is not None or kind == "exceed_context_size_error":
            # Contract 5.4: never a generic transport failure, never
            # model_invalid. The operator message carries both numbers.
            self._last_overflow = overflow
            return OllamaStatus.CONTEXT_EXCEEDED
        if status in {401, 403}:
            return OllamaStatus.IDENTITY_MISMATCH
        if status in {429, 503} or kind in {"server_error", "unavailable_error"}:
            return OllamaStatus.RESOURCE_BUSY
        if 300 <= status <= 399:
            return OllamaStatus.PROTOCOL_VIOLATION
        if 500 <= status <= 599:
            return OllamaStatus.TRANSPORT_UNAVAILABLE
        return OllamaStatus.PROTOCOL_VIOLATION

    def _read_response(
        self, response: Any, intent: HttpIntent, cancel: CancelProbe, started: float,
    ) -> object:
        return self._read_all(response, cancel, started)

    # ---- helpers --------------------------------------------------------

    def _json(self, url: str, kind: str, cancel: CancelProbe) -> dict[str, Any]:
        import json

        intent = HttpIntent("GET", url, None, _JSON, kind)
        value, status = self._execute(intent, cancel, None)
        if status is not None:
            raise BackendError(f"{kind} request failed: {status.value}")
        try:
            payload = json.loads(require_bytes(value).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BackendError(f"{kind} response is not JSON") from None
        if type(payload) is not dict:
            raise BackendError(f"{kind} response is not an object")
        return payload

    def _result_from(self, raw: bytes, request: ChatRequest) -> ChatResult:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        try:
            outcome = protocol.parse_sse_stream(text.splitlines())
        except BackendError:
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        if outcome.reasoning_only:
            # Contract 7.4: empty content beside a reasoning channel is an
            # Analyst misconfiguration, not the model failing a schema.
            return ChatResult(OllamaStatus.CONFIGURATION_FAILURE)
        if not outcome.content:
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        metrics = _metrics(outcome, len(raw))
        if outcome.finish_reason not in {"stop", "length"}:
            return ChatResult(OllamaStatus.PROTOCOL_VIOLATION)
        if outcome.finish_reason == "length":
            return ChatResult(OllamaStatus.MODEL_INVALID, metrics=metrics)
        return ChatResult(
            OllamaStatus.SUCCESS, content=outcome.content, metrics=metrics,
        )


def _encode(body: dict[str, Any]) -> bytes:
    import json

    return json.dumps(
        body, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _text(value: object) -> str | None:
    return value if type(value) is str and value else None


def _count(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _metrics(outcome: protocol.StreamOutcome, raw_bytes: int) -> ChatMetrics:
    content_bytes = len(outcome.content.encode("utf-8"))
    return ChatMetrics(
        done_reason=outcome.finish_reason or "stop",
        prompt_eval_count=outcome.prompt_tokens,
        eval_count=outcome.predicted_tokens,
        total_duration_ns=int((outcome.prompt_ms + outcome.predicted_ms) * 1e6),
        load_duration_ns=0,
        prompt_eval_duration_ns=int(outcome.prompt_ms * 1e6),
        eval_duration_ns=int(outcome.predicted_ms * 1e6),
        raw_body_bytes=max(raw_bytes, content_bytes),
        content_bytes=content_bytes,
        # Never populated: the reasoning channel is disabled on every request
        # and its text is never retained (contract 7.4, erratum E1).
        thinking_bytes=0,
    )


__all__ = ["OpenAICompatBackend"]
