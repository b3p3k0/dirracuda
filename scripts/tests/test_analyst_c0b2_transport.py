"""Offline tests for the bounded C0B-2 Ollama transport.

Every HTTP object is fake.  This module must never contact a real Ollama daemon.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import pytest

from scripts.analyst_benchmark import c0b2_schema as schema
from scripts.analyst_benchmark import c0b2_transport as tx
from scripts.analyst_benchmark.c0b2_executor import (
    SERVER_CONTROL_MODEL, ControlRequest, ProvenanceFailure, RetryableTransport,
    SafetyLimit, WorkRequest,
)

MODEL = "model:1"
DIGEST = "a" * 64
VERSION = "0.32.5"
CONFIG_HASH = "b" * 64


class FakeRaw:
    def __init__(self, chunks: list[bytes] | None = None,
                 error: BaseException | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.calls: list[tuple[int, bool]] = []

    def stream(self, *, amt: int, decode_content: bool):
        self.calls.append((amt, decode_content))
        if self.error is not None:
            raise self.error
        yield from self.chunks


class BlockingRaw:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.closed = threading.Event()

    def stream(self, *, amt: int, decode_content: bool):
        self.started.set()
        if not self.closed.wait(2):
            raise AssertionError("transport did not close the blocked response")
        raise OSError("closed")
        yield b""  # pragma: no cover


class UncooperativeBlockingRaw:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def stream(self, *, amt: int, decode_content: bool):
        self.started.set()
        self.release.wait(2)
        yield frame(content=valid_answer())


class TricklingRaw:
    def stream(self, *, amt: int, decode_content: bool):
        while True:
            time.sleep(0.005)
            yield b" "


class StubHTTPResponse:
    def __init__(self, chunks: list[bytes] | None = None, *, status: int = 200,
                 content_type: str = "application/x-ndjson",
                 encoding: str = "", raw: Any | None = None) -> None:
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if encoding:
            self.headers["Content-Encoding"] = encoding
        self.raw = raw or FakeRaw(chunks)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        closed = getattr(self.raw, "closed", None)
        if isinstance(closed, threading.Event):
            closed.set()


class BlockingCloseResponse(StubHTTPResponse):
    def __init__(self, *, raw: Any) -> None:
        super().__init__(raw=raw)
        self.close_started = threading.Event()
        self.close_release = threading.Event()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        if not self.close_release.wait(2):
            raise AssertionError("response close was not released")
        super().close()


class FakeSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.trust_env = True
        self.max_redirects = 30

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class BlockingHeaderSession(FakeSession):
    def __init__(self, response: StubHTTPResponse) -> None:
        super().__init__([])
        self.response = response
        self.started = threading.Event()
        self.release = threading.Event()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        self.calls.append((method, url, kwargs))
        self.started.set()
        self.release.wait(2)
        return self.response


def wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def wait_for_request_worker_exit(timeout: float = 1.0) -> bool:
    def worker_exited() -> bool:
        if not tx._REQUEST_WORKER_SLOT.acquire(blocking=False):
            return False
        tx._REQUEST_WORKER_SLOT.release()
        return True

    return wait_until(worker_exited, timeout)


def options() -> dict[str, int | float]:
    return {
        "temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0,
        "repeat_penalty": 1.0, "repeat_last_n": 0, "seed": 1,
        "num_ctx": 8192, "num_predict": 4096,
    }


def chat_spec(*, prompt: str = "test", cancel_on_first: bool = False,
              payload_mutation=None) -> tx.RequestSpec:
    payload = {
        "model": MODEL, "messages": [{"role": "user", "content": prompt}],
        "stream": True, "format": schema.worksheet_schema("v2"),
        "options": options(), "think": False, "keep_alive": "15m",
    }
    if payload_mutation:
        payload_mutation(payload)
    return tx.RequestSpec(
        kind="chat", payload=payload, worksheet="v2", expected_model=MODEL,
        expected_digest=DIGEST, cancel_on_first_content=cancel_on_first,
    )


def work_for(spec: tx.RequestSpec, *, request_hash: str | None = None) -> WorkRequest:
    return WorkRequest("C", "work", MODEL,
                       request_hash or tx.request_spec_hash(spec), 1)


def control_for(spec: tx.RequestSpec, *, model: str = MODEL,
                request_hash: str | None = None) -> ControlRequest:
    if spec.kind in {"version", "tags"} and model == MODEL:
        model = SERVER_CONTROL_MODEL
    return ControlRequest("C", "control", model,
                          request_hash or tx.request_spec_hash(spec), 1)


def valid_answer() -> str:
    return json.dumps({
        "document_type": "record", "subject": "", "assessment": "no_findings",
        "findings": [],
    }, separators=(",", ":"))


def frame(*, content: str = "", thinking: str = "", done: bool = True,
          model: str = MODEL, done_reason: str = "stop", **extra: Any) -> bytes:
    value: dict[str, Any] = {
        "model": model,
        "message": {"role": "assistant", "content": content, "thinking": thinking},
        "done": done,
    }
    if done:
        value.update({
            "done_reason": done_reason, "total_duration": 10, "load_duration": 1,
            "prompt_eval_count": 20, "prompt_eval_duration": 2,
            "eval_count": 5, "eval_duration": 3,
        })
    value.update(extra)
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def json_response(value: Any, *, status: int = 200) -> StubHTTPResponse:
    return StubHTTPResponse(
        [json.dumps(value, separators=(",", ":")).encode()], status=status,
        content_type="application/json",
    )


def invoke(spec: tx.RequestSpec, response: Any, *, request=None,
           validator=tx.default_schema_validator, monotonic=None):
    session = FakeSession([response])
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=session, schema_validator=validator,
        **({"monotonic": monotonic} if monotonic else {}),
    )
    durable = request or (work_for(spec) if spec.kind == "chat" else control_for(spec))
    result = transport(durable, threading.Event())
    return result, session, transport


def test_successful_chat_is_bounded_sanitized_and_session_hardened() -> None:
    answer = valid_answer()
    raw = frame(content=answer[:20], thinking="private-reasoning", done=False) \
        + frame(content=answer[20:])
    response = StubHTTPResponse([raw[:17], raw[17:71], raw[71:]])

    result, session, _transport = invoke(chat_spec(), response)

    assert result.outcome == "ACCEPTED" and result.content == answer
    assert result.metadata["thinking_bytes"] == len("private-reasoning")
    assert "private-reasoning" not in json.dumps(dict(result.metadata))
    assert result.metadata["canonical_content_sha256"] == hashlib.sha256(
        schema.canonical_json(json.loads(answer))).hexdigest()
    assert response.closed
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", tx.EXACT_ENDPOINT + "/api/chat")
    assert kwargs["timeout"] == (10.0, 180.0)
    assert kwargs["allow_redirects"] is False
    assert kwargs["proxies"] == {"http": None, "https": None}
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert session.trust_env is False and session.max_redirects == 0
    assert response.raw.calls == [(64 * 1024, False)]


def test_done_reason_length_is_accepted_measured_evidence() -> None:
    result, _session, _transport = invoke(
        chat_spec(), StubHTTPResponse([frame(content=valid_answer(), done_reason="length")]))
    assert result.outcome == "ACCEPTED"
    assert result.metadata["done_reason"] == "length"


@pytest.mark.parametrize("endpoint", [
    "http://localhost:11434", "http://127.0.0.2:11434",
    "http://127.0.0.1:11435", "https://127.0.0.1:11434",
    "http://127.0.0.1:11434/", "http://127.0.0.1:11434?x=1",
    "http://[::1]:11434",
])
def test_only_exact_endpoint_is_allowed(endpoint: str) -> None:
    with pytest.raises(ProvenanceFailure, match="endpoint_mismatch"):
        tx.BoundedOllamaTransport(lambda _request: chat_spec(), endpoint=endpoint,
                                  session=FakeSession([]))


def test_request_hash_mismatch_fails_before_http() -> None:
    spec = chat_spec()
    session = FakeSession([])
    transport = tx.BoundedOllamaTransport(lambda _request: spec, session=session)
    with pytest.raises(ProvenanceFailure, match="request_hash_mismatch"):
        transport(work_for(spec, request_hash="f" * 64), threading.Event())
    assert session.calls == []


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(stream=False),
    lambda p: p.update(keep_alive="0"),
    lambda p: p.update(tools=[]),
    lambda p: p["messages"][0].update(images=[]),
    lambda p: p["options"].update(num_ctx=True),
    lambda p: p["options"].update(temperature=0),
    lambda p: p.update(format={"type": "object"}),
])
def test_invalid_chat_contract_fails_before_http(mutation) -> None:
    spec = chat_spec(payload_mutation=mutation)
    session = FakeSession([])
    transport = tx.BoundedOllamaTransport(lambda _request: spec, session=session)
    with pytest.raises(ProvenanceFailure):
        transport(work_for(spec), threading.Event())
    assert not session.calls


def test_scored_work_cannot_enable_cancellation_probe_mode() -> None:
    spec = chat_spec(cancel_on_first=True)
    with pytest.raises(ProvenanceFailure, match="scored_work_cannot_self_cancel"):
        invoke(spec, StubHTTPResponse([frame(content="x", done=False)]))


def test_prompt_limit_accepts_boundary_and_rejects_next_byte() -> None:
    at_limit = chat_spec(prompt="x" * tx.MAX_PROMPT_BYTES)
    result, *_ = invoke(at_limit, StubHTTPResponse([frame(content=valid_answer())]))
    assert result.outcome == "ACCEPTED"
    over = chat_spec(prompt="x" * (tx.MAX_PROMPT_BYTES + 1))
    with pytest.raises(SafetyLimit, match="prompt_limit"):
        invoke(over, StubHTTPResponse([frame(content=valid_answer())]))


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_retryable_http_statuses(status: int) -> None:
    response = json_response({"error": "temporary"}, status=status)
    with pytest.raises(RetryableTransport, match="retryable_http_status"):
        invoke(chat_spec(), response)


@pytest.mark.parametrize("status", [300, 301, 307, 400, 404, 422])
def test_redirects_and_ordinary_client_errors_are_provenance(status: int) -> None:
    response = json_response({"error": "bad configuration"}, status=status)
    with pytest.raises(ProvenanceFailure):
        invoke(chat_spec(), response)


def test_explicit_oom_client_error_is_retryable_without_leaking_message() -> None:
    response = json_response({"error": "CUDA out of memory: sensitive detail"}, status=400)
    with pytest.raises(RetryableTransport) as caught:
        invoke(chat_spec(), response)
    assert str(caught.value) == "resource_error"
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionError(), OSError()])
def test_transport_exceptions_are_retryable(error: BaseException) -> None:
    with pytest.raises(RetryableTransport, match="transport_error"):
        invoke(chat_spec(), error)


@pytest.mark.parametrize(("content_type", "encoding", "reason"), [
    ("application/json", "", "unexpected_content_type"),
    ("application/x-ndjson", "gzip", "unexpected_content_encoding"),
])
def test_chat_rejects_content_type_and_encoding(
        content_type: str, encoding: str, reason: str) -> None:
    response = StubHTTPResponse([frame(content=valid_answer())],
                                content_type=content_type, encoding=encoding)
    with pytest.raises(SafetyLimit, match=reason):
        invoke(chat_spec(), response)


@pytest.mark.parametrize("raw", [
    b"not-json\n",
    b'{"model":"model:1","model":"model:1"}\n',
    b'{"model":"model:1","value":NaN}\n',
    b"\xff\n",
])
def test_malformed_ndjson_is_a_nonretryable_safety_failure(raw: bytes) -> None:
    with pytest.raises(SafetyLimit, match="invalid_wire_json"):
        invoke(chat_spec(), StubHTTPResponse([raw]))


def test_overlong_unterminated_frame_is_rejected_at_limit() -> None:
    raw = b"x" * (tx.MAX_FRAME_BYTES + 1)
    with pytest.raises(SafetyLimit, match="ndjson_frame_limit"):
        invoke(chat_spec(), StubHTTPResponse([raw]))


def test_exact_frame_boundary_reaches_parser_instead_of_frame_limit() -> None:
    base = frame(content=valid_answer()).rstrip(b"\n")
    raw = base + b" " * (tx.MAX_FRAME_BYTES - len(base)) + b"\n"
    result, *_ = invoke(chat_spec(), StubHTTPResponse([raw]))
    assert result.outcome == "ACCEPTED"


def test_cumulative_body_limit_is_incremental() -> None:
    chunks = [b"\n" * tx.MAX_FRAME_BYTES for _ in range(4)] + [b"x"]
    with pytest.raises(SafetyLimit, match="http_body_limit"):
        invoke(chat_spec(), StubHTTPResponse(chunks))


def test_content_and_combined_channel_caps() -> None:
    with pytest.raises(SafetyLimit, match="answer_content_limit"):
        invoke(chat_spec(), StubHTTPResponse([
            frame(content="x" * (tx.MAX_CONTENT_BYTES + 1))]))
    pieces = [frame(thinking="x" * 220_000, done=False) for _ in range(4)]
    pieces.append(frame(content=valid_answer(), thinking="x" * 220_000))
    with pytest.raises(SafetyLimit, match="combined_channel_limit"):
        invoke(chat_spec(), StubHTTPResponse(pieces))


def test_answer_depth_and_node_caps() -> None:
    deep: Any = 0
    for _ in range(tx.MAX_JSON_DEPTH):
        deep = [deep]
    with pytest.raises(SafetyLimit, match="json_depth_limit"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=json.dumps(deep))]))
    many = [0] * tx.MAX_JSON_NODES
    with pytest.raises(SafetyLimit, match="json_node_limit"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=json.dumps(many))]))
    recursive = "[" * 2000 + "0" + "]" * 2000
    with pytest.raises(SafetyLimit, match="json_depth_limit"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=recursive)]))


@pytest.mark.parametrize(("mutation", "error"), [
    (lambda f: f["message"].update(tool_calls=[{"function": {}}]),
     "forbidden_message_channel"),
    (lambda f: f["message"].update(images=["image"]), "forbidden_message_channel"),
    (lambda f: f["message"].update(secret="value"), "unknown_message_channel"),
    (lambda f: f.update(logprobs=[{"token": "x"}]), "forbidden_logprobs_channel"),
])
def test_forbidden_channels_fail_safety(mutation, error: str) -> None:
    value = json.loads(frame(content=valid_answer()))
    mutation(value)
    raw = json.dumps(value, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(SafetyLimit, match=error):
        invoke(chat_spec(), StubHTTPResponse([raw]))


def test_empty_unknown_message_field_is_measured_as_empty() -> None:
    value = json.loads(frame(content=valid_answer()))
    value["message"]["future"] = []
    result, *_ = invoke(
        chat_spec(), StubHTTPResponse([
            json.dumps(value, separators=(",", ":")).encode() + b"\n"]))
    assert result.metadata["unknown_message_fields_empty"] is True


@pytest.mark.parametrize(("chunks", "exception", "match"), [
    ([frame(content=valid_answer(), model="other")], ProvenanceFailure,
     "response_model_mismatch"),
    ([frame(content=valid_answer(), done=False)], RetryableTransport,
     "stream_ended_without_done"),
    ([frame(content=valid_answer()), frame(content="{}")], SafetyLimit,
     "frame_after_done"),
    ([b'{"error":"server failed"}\n'], RetryableTransport, "stream_error"),
])
def test_model_done_and_stream_error_contract(chunks, exception, match: str) -> None:
    with pytest.raises(exception, match=match):
        invoke(chat_spec(), StubHTTPResponse(chunks))


def test_answer_json_syntax_and_duplicate_keys_are_schema_invalid() -> None:
    for answer in ("not-json", '{"a":1,"a":2}'):
        result, *_ = invoke(chat_spec(), StubHTTPResponse([frame(content=answer)]))
        assert result.outcome == "SCHEMA_INVALID"
        assert result.metadata["strict_schema_invalid"] is True
        assert result.metadata["semantic_invalid"] is False


def test_schema_classification_is_strict_first_and_mutually_exclusive() -> None:
    semantic = {
        "document_type": "record", "subject": "",
        "assessment": "no_findings",
        "findings": [{"category": "pii", "quote": "123", "offset": 0}],
    }
    result, *_ = invoke(
        chat_spec(), StubHTTPResponse([frame(content=json.dumps(semantic))]))
    assert result.outcome == "SCHEMA_INVALID"
    assert result.metadata["strict_schema_invalid"] is False
    assert result.metadata["semantic_invalid"] is True

    both = dict(semantic, subject=12)
    result, *_ = invoke(
        chat_spec(), StubHTTPResponse([frame(content=json.dumps(both))]))
    assert result.metadata["strict_schema_invalid"] is True
    assert result.metadata["semantic_invalid"] is False


def test_schema_callback_result_and_failures_are_bounded() -> None:
    def invalid(_worksheet, _value):
        return tx.SchemaAssessment(False, True)

    result, *_ = invoke(chat_spec(), StubHTTPResponse([frame(content=valid_answer())]),
                        validator=invalid)
    assert result.outcome == "SCHEMA_INVALID"
    with pytest.raises(SafetyLimit, match="schema_validator_failed"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=valid_answer())]),
               validator=lambda _w, _v: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(SafetyLimit, match="schema_validator_type"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=valid_answer())]),
               validator=lambda _w, _v: True)
    with pytest.raises(SafetyLimit, match="schema_validator_classification"):
        invoke(chat_spec(), StubHTTPResponse([frame(content=valid_answer())]),
               validator=lambda _w, _v: tx.SchemaAssessment(True, True))


def version_spec(expected: str = VERSION) -> tx.RequestSpec:
    return tx.RequestSpec(kind="version", expected_version=expected)


def tags_spec(models=None) -> tx.RequestSpec:
    return tx.RequestSpec(kind="tags", expected_models=models or {MODEL: DIGEST})


def show_spec() -> tx.RequestSpec:
    return tx.RequestSpec(kind="show", expected_model=MODEL, expected_digest=DIGEST)


def ps_spec(*, context: int = 8192) -> tx.RequestSpec:
    return tx.RequestSpec(
        kind="ps", expected_model=MODEL, expected_digest=DIGEST,
        min_context=context, purpose="stage_c_context", config_sha256=CONFIG_HASH,
    )


def test_version_control_is_exact_bounded_and_sanitized() -> None:
    spec = version_spec()
    result, session, _ = invoke(spec, json_response({"version": VERSION}))
    assert json.loads(result.content) == {"version": VERSION}
    assert session.calls[0][:2] == ("GET", tx.EXACT_ENDPOINT + "/api/version")
    with pytest.raises(ProvenanceFailure, match="ollama_version_mismatch"):
        invoke(spec, json_response({"version": "0.32.6"}))


def test_tags_require_unique_exact_full_digests() -> None:
    row = {"name": MODEL, "model": MODEL, "digest": DIGEST,
           "size": 1, "details": {"family": "test"}}
    result, *_ = invoke(tags_spec(), json_response({"models": [row]}))
    assert json.loads(result.content) == {
        "models": [{"name": MODEL, "digest": DIGEST}]}
    for rows in ([row, row], [dict(row, digest="c" * 64)],
                 [dict(row, model="alias")]):
        with pytest.raises(ProvenanceFailure):
            invoke(tags_spec(), json_response({"models": rows}))
    with pytest.raises(ProvenanceFailure, match="cloud_model_refused"):
        cloud = tags_spec({"unsafe:cloud": DIGEST})
        invoke(cloud, json_response({"models": []}))


def test_show_posts_nonverbose_and_persists_only_sanitized_hashes() -> None:
    value = {
        "parameters": "temperature 0.7", "template": "secret template",
        "license": "large license", "capabilities": ["completion"],
        "details": {"family": "test", "format": "gguf", "unsafe": "omit"},
        "model_info": {"context_length": 8192},
    }
    result, session, _ = invoke(show_spec(), json_response(value))
    safe = json.loads(result.content)
    assert safe["model"] == MODEL and safe["digest"] == DIGEST
    assert safe["details"] == {"format": "gguf", "family": "test"}
    assert "secret" not in result.content and "license" not in result.content
    assert session.calls[0][2]["json"] == {"model": MODEL, "verbose": False}


def test_show_has_a_control_specific_node_cap_for_nonverbose_tensor_metadata() -> None:
    current_ollama_shape = {"tensors": [0] * (tx.MAX_JSON_NODES + 10)}
    result, *_ = invoke(show_spec(), json_response(current_ollama_shape))
    assert json.loads(result.content)["model"] == MODEL
    exact_boundary = {"tensors": [0] * (tx.MAX_SHOW_JSON_NODES - 3)}
    assert invoke(show_spec(), json_response(exact_boundary))[0].outcome == "ACCEPTED"
    with pytest.raises(SafetyLimit, match="json_node_limit"):
        invoke(show_spec(), json_response(
            {"tensors": [0] * (tx.MAX_SHOW_JSON_NODES - 2)}))
    with pytest.raises(SafetyLimit, match="json_node_limit"):
        invoke(version_spec(), json_response(
            {"version": VERSION, "padding": [0] * tx.MAX_JSON_NODES}))
    deep: Any = 0
    for _ in range(tx.MAX_JSON_DEPTH):
        deep = [deep]
    with pytest.raises(SafetyLimit, match="json_depth_limit"):
        invoke(show_spec(), json_response({"tensors": deep}))
    with pytest.raises(SafetyLimit, match="canonical_json_limit"):
        invoke(show_spec(), json_response(
            {"license": "x" * tx.MAX_CANONICAL_JSON_BYTES}))


def test_control_response_canonical_cap_is_enforced() -> None:
    value = {"version": VERSION, "padding": "x" * tx.MAX_CANONICAL_JSON_BYTES}
    with pytest.raises(SafetyLimit, match="canonical_json_limit"):
        invoke(version_spec(), json_response(value))


def ps_value(*, digest: str = DIGEST, context: Any = 8192) -> dict[str, Any]:
    return {"models": [{
        "name": MODEL, "model": MODEL, "digest": digest,
        "size": 100, "size_vram": 60, "context_length": context,
    }]}


def test_ps_requires_exact_identity_and_context_and_is_sanitized() -> None:
    spec = ps_spec()
    result, session, _ = invoke(spec, json_response(ps_value()))
    assert json.loads(result.content) == {
        "purpose": "stage_c_context", "config_sha256": CONFIG_HASH,
        "model": MODEL, "digest": DIGEST, "size": 100,
        "size_vram": 60, "context_length": 8192,
    }
    assert session.calls[0][:2] == ("GET", tx.EXACT_ENDPOINT + "/api/ps")
    for bad in (ps_value(digest="c" * 64), ps_value(context=4096),
                ps_value(context=True), {"models": []},
                {"models": ps_value()["models"] * 2}):
        with pytest.raises(ProvenanceFailure):
            invoke(spec, json_response(bad))


def test_control_hash_is_canonical_and_rejects_irrelevant_fields() -> None:
    first = ps_spec()
    second = tx.RequestSpec(
        kind="ps", purpose="stage_c_context", config_sha256=CONFIG_HASH,
        min_context=8192, expected_digest=DIGEST, expected_model=MODEL,
    )
    assert tx.request_spec_hash(first) == tx.request_spec_hash(second)
    invalid = tx.RequestSpec(kind="version", expected_version=VERSION,
                             expected_models={})
    with pytest.raises(ProvenanceFailure, match="version_spec_shape"):
        invoke(invalid, json_response({"version": VERSION}))


def test_cancellation_before_request_has_zero_http_side_effects() -> None:
    spec = chat_spec()
    session = FakeSession([])
    transport = tx.BoundedOllamaTransport(lambda _request: spec, session=session)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RetryableTransport, match="cancelled"):
        transport(work_for(spec), cancel)
    assert not session.calls


def test_control_cancellation_on_first_content_closes_stream() -> None:
    spec = chat_spec(cancel_on_first=True)
    response = StubHTTPResponse([
        frame(content="first", done=False), frame(content=valid_answer())])
    cancel = threading.Event()
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=FakeSession([response]))
    with pytest.raises(RetryableTransport, match="cancelled"):
        transport(control_for(spec), cancel)
    assert cancel.is_set() and wait_until(lambda: response.closed)
    assert wait_for_request_worker_exit()


def test_external_cancellation_closes_a_blocked_current_response() -> None:
    spec = chat_spec()
    raw = BlockingRaw()
    response = StubHTTPResponse(raw=raw)
    cancel = threading.Event()
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=FakeSession([response]))
    caught: list[BaseException] = []

    def run() -> None:
        try:
            transport(work_for(spec), cancel)
        except BaseException as exc:  # test thread reports exact result to parent
            caught.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert raw.started.wait(1)
    cancel.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(caught) == 1 and isinstance(caught[0], RetryableTransport)
    assert str(caught[0]) == "cancelled" and response.closed
    assert wait_for_request_worker_exit()


def test_cancel_current_never_blocks_or_duplicates_a_blocked_close() -> None:
    spec = chat_spec()
    raw = BlockingRaw()
    response = BlockingCloseResponse(raw=raw)
    cancel = threading.Event()
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=FakeSession([response]))
    caught: list[BaseException] = []

    def run() -> None:
        try:
            transport(work_for(spec), cancel)
        except BaseException as exc:
            caught.append(exc)

    caller = threading.Thread(target=run)
    caller.start()
    assert raw.started.wait(1)
    cancel.set()
    started = time.monotonic()
    transport.cancel_current()
    assert time.monotonic() - started < 0.2
    assert response.close_started.wait(1)
    transport.cancel_current()
    assert response.close_calls == 1
    caller.join(1)
    assert not caller.is_alive()
    assert len(caught) == 1 and str(caught[0]) == "cancelled"
    assert not response.closed
    response.close_release.set()
    assert wait_until(lambda: response.closed)
    assert wait_for_request_worker_exit()
    assert transport._active_response is None
    assert transport._close_target is None and transport._close_done is None


def test_total_deadline_bounds_blocked_header_and_late_response(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tx, "TOTAL_REQUEST_SECONDS", 0.05)
    spec = chat_spec()
    response = StubHTTPResponse([frame(content=valid_answer())])
    session = BlockingHeaderSession(response)
    transport = tx.BoundedOllamaTransport(lambda _request: spec, session=session)
    started = time.monotonic()
    try:
        with pytest.raises(RetryableTransport, match="request_timeout"):
            transport(work_for(spec), threading.Event())
        assert time.monotonic() - started < 0.5
        assert session.started.is_set()

        other = FakeSession([StubHTTPResponse([frame(content=valid_answer())])])
        blocked = tx.BoundedOllamaTransport(lambda _request: spec, session=other)
        with pytest.raises(RetryableTransport, match="transport_error"):
            blocked(work_for(spec), threading.Event())
        assert other.calls == []
    finally:
        session.release.set()
    assert wait_until(lambda: response.closed)
    assert wait_for_request_worker_exit()


def test_total_deadline_bounds_body_that_ignores_close(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tx, "TOTAL_REQUEST_SECONDS", 0.05)
    raw = UncooperativeBlockingRaw()
    response = StubHTTPResponse(raw=raw)
    spec = chat_spec()
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=FakeSession([response]))
    started = time.monotonic()
    try:
        with pytest.raises(RetryableTransport, match="request_timeout"):
            transport(work_for(spec), threading.Event())
        assert time.monotonic() - started < 0.5
        assert raw.started.is_set() and wait_until(lambda: response.closed)
    finally:
        raw.release.set()
    assert wait_until(lambda: response.closed)
    assert wait_for_request_worker_exit()


def test_total_deadline_is_not_extended_by_trickling_body(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tx, "TOTAL_REQUEST_SECONDS", 0.05)
    response = StubHTTPResponse(raw=TricklingRaw())
    with pytest.raises(RetryableTransport, match="request_timeout"):
        invoke(chat_spec(), response)
    assert response.closed
    assert wait_for_request_worker_exit()


def test_operator_cancel_wins_when_deadline_is_also_due() -> None:
    spec = chat_spec()
    response = StubHTTPResponse([frame(content=valid_answer())])
    session = BlockingHeaderSession(response)
    cancel = threading.Event()
    overdue = threading.Event()
    transport = tx.BoundedOllamaTransport(
        lambda _request: spec, session=session,
        monotonic=lambda: tx.TOTAL_REQUEST_SECONDS + 1 if overdue.is_set() else 0.0,
    )
    caught: list[BaseException] = []

    def run() -> None:
        try:
            transport(work_for(spec), cancel)
        except BaseException as exc:
            caught.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert session.started.wait(1)
        cancel.set()
        overdue.set()
        thread.join(1)
        assert not thread.is_alive()
        assert len(caught) == 1 and str(caught[0]) == "cancelled"
    finally:
        session.release.set()
    assert wait_until(lambda: response.closed)
    assert wait_for_request_worker_exit()


def test_missing_raw_stream_and_nonbytes_chunks_fail_safety() -> None:
    response = StubHTTPResponse([frame(content=valid_answer())])
    response.raw = object()
    with pytest.raises(SafetyLimit, match="response_has_no_raw_stream"):
        invoke(chat_spec(), response)
    response = StubHTTPResponse([])
    response.raw = FakeRaw(["not bytes"])  # type: ignore[list-item]
    with pytest.raises(SafetyLimit, match="non_bytes_http_body"):
        invoke(chat_spec(), response)
