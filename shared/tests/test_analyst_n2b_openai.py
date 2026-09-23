"""N2b: the OpenAI-compatible adapter and its pure protocol.

Every shape asserted here was measured against llama.cpp build b1-f280b26.
"""

from __future__ import annotations

import json

import pytest

from experimental.analyst.backends import openai_protocol as proto
from experimental.analyst.backends.base import UNKNOWN_CONTEXT, BackendError
from experimental.analyst.backends.openai_api import OpenAICompatBackend
from experimental.analyst.endpoint import AddressPolicyError
from experimental.analyst.ollama_contract import (
    GENERATION_OPTIONS,
    OllamaStatus,
    build_chat_request,
)

_NONCE = "FENCE_0123456789ABCDEF"
_MIMIR = "http://100.125.197.36:9292"


def _model(**over):
    base = dict(
        model_id="qwen3.8-27b", status="sleeping",
        args=("--ctx-size", "32768"), input_modalities=("text",),
        output_modalities=("text",), meta=None,
    )
    base.update(over)
    return proto.ServerModel(**base)


# --------------------------------------------------------------------------
# Context discovery (contract 5.3) -- the part most likely to be got wrong
# --------------------------------------------------------------------------

def test_a_router_reporting_zero_is_unknown_not_a_limit():
    props = {"role": "router", "default_generation_settings": {"n_ctx": 0}}
    assert proto.discover_context(props, None) == UNKNOWN_CONTEXT


def test_a_router_reads_context_from_the_child_args_when_unloaded():
    props = {"role": "router", "default_generation_settings": {"n_ctx": 0}}
    assert proto.discover_context(props, _model()) == 32768


def test_a_router_prefers_loaded_meta_over_the_args():
    props = {"role": "router"}
    model = _model(args=("--ctx-size", "8192"), meta={"n_ctx": 32768})
    assert proto.discover_context(props, model) == 32768


def test_a_single_server_reads_its_own_props():
    assert proto.discover_context(
        {"default_generation_settings": {"n_ctx": 32768}}, None
    ) == 32768


@pytest.mark.parametrize("value", [0, -1, None, "32768", 3.5])
def test_a_bad_context_value_is_unknown_never_unlimited(value):
    assert proto.discover_context(
        {"default_generation_settings": {"n_ctx": value}}, None
    ) == UNKNOWN_CONTEXT


def test_context_is_never_divided_by_slots():
    """Contract 5.1: the division is version-dependent and did not occur on
    the probed build, so neither rule may be hardcoded."""
    props = {"default_generation_settings": {"n_ctx": 32768}, "total_slots": 4}
    assert proto.discover_context(props, None) == 32768


# --------------------------------------------------------------------------
# Model filtering (contract 6.3)
# --------------------------------------------------------------------------

def test_an_embeddings_server_is_never_offered():
    assert not _model(args=("--embeddings", "--pooling", "mean")).is_text_generation


def test_a_vision_model_is_never_offered():
    assert not _model(args=("--mmproj", "/x"),
                      input_modalities=("text", "image")).is_text_generation


def test_a_text_model_is_offered():
    assert _model().is_text_generation


def test_parse_models_rejects_a_malformed_catalogue():
    for bad in ({}, {"data": {}}, {"data": [{"no_id": 1}]}, {"data": [{"id": ""}]}):
        with pytest.raises(BackendError):
            proto.parse_models(bad)


# --------------------------------------------------------------------------
# The error taxonomy (contract 4, 5.4)
# --------------------------------------------------------------------------

def test_a_context_overflow_carries_both_numbers():
    body = json.dumps({"error": {
        "code": 400, "type": "exceed_context_size_error",
        "message": "too big", "n_prompt_tokens": 60012, "n_ctx": 32768,
    }}).encode()
    kind, overflow = proto.parse_error(body)
    assert kind == "exceed_context_size_error"
    assert overflow.prompt_tokens == 60012
    assert overflow.context_tokens == 32768


def test_the_overflow_message_names_both_numbers_and_the_fix():
    overflow = proto.ContextOverflow(prompt_tokens=60012, context_tokens=32768)
    message = overflow.message("qwen3.8-27b", "mimir")
    assert "60012" in message and "32768" in message
    assert "qwen3.8-27b" in message and "--ctx-size" in message


def test_a_non_overflow_error_yields_its_type_only():
    body = json.dumps({"error": {"code": 500, "type": "server_error"}}).encode()
    assert proto.parse_error(body) == ("server_error", None)


def test_garbage_error_bodies_are_survivable():
    for bad in (b"", b"not json", b"[]", b'{"error": 3}'):
        assert proto.parse_error(bad) == (None, None)


# --------------------------------------------------------------------------
# The SSE stream (measured shape)
# --------------------------------------------------------------------------

def _frame(**over):
    base = {"choices": [{"index": 0, "finish_reason": None, "delta": {}}]}
    base.update(over)
    return "data: " + json.dumps(base)


def test_a_streamed_completion_assembles_its_content():
    lines = [
        _frame(choices=[{"index": 0, "finish_reason": None,
                         "delta": {"content": '{"a":'}}]),
        _frame(choices=[{"index": 0, "finish_reason": None,
                         "delta": {"content": "1}"}}]),
        _frame(choices=[{"index": 0, "finish_reason": "stop", "delta": {}}],
               system_fingerprint="b1-f280b26",
               timings={"prompt_n": 15, "predicted_n": 3,
                        "prompt_ms": 350.7, "predicted_ms": 166.9}),
        "data: [DONE]",
    ]
    out = proto.parse_sse_stream(lines)
    assert out.content == '{"a":1}'
    assert out.finish_reason == "stop"
    assert out.system_fingerprint == "b1-f280b26"
    assert (out.prompt_tokens, out.predicted_tokens) == (15, 3)
    assert out.reasoning_only is False


def test_streaming_token_counts_come_from_timings_not_usage():
    """Measured: a streamed llama.cpp completion carries no usage block."""
    out = proto.parse_sse_stream([
        _frame(choices=[{"index": 0, "finish_reason": "stop",
                         "delta": {"content": "x"}}],
               timings={"prompt_n": 42, "predicted_n": 7}),
        "data: [DONE]",
    ])
    assert out.prompt_tokens == 42 and out.predicted_tokens == 7


def test_reasoning_text_is_never_retained():
    """Contract 7.4 and erratum E1: only the fact, never the text."""
    out = proto.parse_sse_stream([
        _frame(choices=[{"index": 0, "finish_reason": "stop",
                         "delta": {"reasoning_content": "SECRET ECHO OF INPUT"}}]),
        "data: [DONE]",
    ])
    assert out.reasoning_only is True
    assert out.content == ""
    assert "SECRET" not in repr(out)


def test_a_malformed_frame_is_refused():
    with pytest.raises(BackendError):
        proto.parse_sse_stream(["data: {not json"])


# --------------------------------------------------------------------------
# Request construction (contract 7.1, 7.2, 7.4, 7.5)
# --------------------------------------------------------------------------

def _body():
    request = build_chat_request("public sample", nonce=_NONCE)
    return proto.build_chat_body(request.payload(), model_id="qwen3.8-27b")


def test_thinking_is_disabled_on_every_request():
    assert _body()["chat_template_kwargs"] == {"enable_thinking": False}


def test_every_sampling_value_is_sent_explicitly():
    """Contract 7.1: the probed server ships temperature 1.0 and top_p 0.95 as
    preset defaults, so nothing may be inherited."""
    body = _body()
    options = GENERATION_OPTIONS.as_payload()
    for field in ("temperature", "top_p", "top_k", "min_p",
                  "repeat_penalty", "repeat_last_n", "seed"):
        assert body[field] == options[field]
    assert body["max_tokens"] == options["num_predict"]


def test_the_schema_is_sent_as_strict_json_schema():
    schema = _body()["response_format"]
    assert schema["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"]["type"] == "object"


def test_the_stream_flag_is_always_set():
    assert _body()["stream"] is True


def test_num_ctx_is_not_sent():
    """llama.cpp fixes context at launch; it is not a per-request field."""
    assert "num_ctx" not in _body()
    assert "options" not in _body()


# --------------------------------------------------------------------------
# Address policy at construction (contract 4.1-4.3)
# --------------------------------------------------------------------------

def test_a_private_endpoint_without_acknowledgement_is_refused():
    with pytest.raises(AddressPolicyError, match="Acknowledge plaintext"):
        OpenAICompatBackend(endpoint=_MIMIR)


def test_a_public_endpoint_is_refused_even_with_acknowledgement():
    with pytest.raises(AddressPolicyError, match="no override"):
        OpenAICompatBackend(endpoint="http://8.8.8.8:9292", plaintext_ack=True)


def test_an_https_endpoint_waits_for_the_security_card():
    with pytest.raises(AddressPolicyError, match="N3"):
        OpenAICompatBackend(endpoint="https://100.125.197.36:9292",
                            plaintext_ack=True)


def test_a_loopback_endpoint_needs_no_acknowledgement():
    backend = OpenAICompatBackend(endpoint="http://127.0.0.1:9292")
    assert backend.endpoint.is_loopback


def test_the_backend_keeps_its_own_timeouts():
    """Review finding M1: the frozen Ollama deadlines must not move."""
    from experimental.analyst import ollama_contract
    from experimental.analyst.ollama_client import OllamaClient

    assert OpenAICompatBackend.IDLE_READ_TIMEOUT == 600.0
    assert OpenAICompatBackend.TOTAL_REQUEST_TIMEOUT == 1800.0
    assert OllamaClient.IDLE_READ_TIMEOUT == ollama_contract.IDLE_READ_TIMEOUT_SECONDS
    assert OllamaClient.TOTAL_REQUEST_TIMEOUT == ollama_contract.TOTAL_REQUEST_SECONDS


def test_both_backends_share_one_in_flight_slot():
    """Contract 11, review finding M2."""
    from experimental.analyst import transport
    from experimental.analyst.ollama_client import OllamaClient

    assert OllamaClient._execute is transport.BoundedHttpClient._execute
    assert OpenAICompatBackend._execute is transport.BoundedHttpClient._execute


# --------------------------------------------------------------------------
# Cancellation wording (contract 7.3)
# --------------------------------------------------------------------------

def test_llama_cpp_may_say_cancelled_plainly():
    """Measured: the slot freed within 4 s, so the hedge is not needed."""
    from experimental.analyst.backends import BackendKind, cancellation_label

    assert cancellation_label(BackendKind.OPENAI_COMPAT) == "cancelled"


def test_ollama_keeps_its_hedged_wording():
    """/api/ps is skewed by keep_alive and cannot prove a stop."""
    from experimental.analyst.backends import BackendKind, cancellation_label

    label = cancellation_label(BackendKind.OLLAMA)
    assert label == "cancel requested; server completion unverified"


# --------------------------------------------------------------------------
# Leak scan: the reasoning channel never reaches an artifact
# --------------------------------------------------------------------------

def test_the_adapter_never_stores_reasoning_text_anywhere():
    """Contract 7.4 and erratum E1. The parser must retain the fact only."""
    secret = "VERBATIM ECHO OF SENSITIVE INPUT"
    out = proto.parse_sse_stream([
        _frame(choices=[{"index": 0, "finish_reason": "stop",
                         "delta": {"reasoning_content": secret}}]),
        "data: [DONE]",
    ])
    fields = {name: getattr(out, name) for name in out.__slots__}
    for surface in (repr(out), str(out), json.dumps(fields, default=str)):
        assert secret not in surface


def test_reasoning_only_answers_are_a_configuration_failure():
    """Never model_invalid, which would blame the model for our own bug."""
    from experimental.analyst.backends.openai_api import OpenAICompatBackend

    backend = OpenAICompatBackend(endpoint="http://127.0.0.1:9292")
    request = build_chat_request("public", nonce=_NONCE)
    raw = ("data: " + json.dumps({"choices": [{
        "index": 0, "finish_reason": "stop",
        "delta": {"reasoning_content": "echo"}}]}) + "\ndata: [DONE]\n").encode()
    result = backend._result_from(raw, request)
    assert result.status is OllamaStatus.CONFIGURATION_FAILURE
    assert result.content is None


def test_a_context_overflow_classifies_as_context_exceeded():
    """Contract 5.4: never a transport failure, never model_invalid."""
    from experimental.analyst.backends.openai_api import OpenAICompatBackend

    class _Resp:
        status_code = 400
        headers = {"content-type": "application/json"}

    backend = OpenAICompatBackend(endpoint="http://127.0.0.1:9292")
    body = json.dumps({"error": {
        "code": 400, "type": "exceed_context_size_error",
        "n_prompt_tokens": 60012, "n_ctx": 32768}}).encode()
    backend._read_all_status = lambda *a, **k: (body, None)
    status = backend._classify_http_status(
        _Resp(), proto and None, lambda: False, 0.0,
    )
    assert status is OllamaStatus.CONTEXT_EXCEEDED
    assert backend._last_overflow.prompt_tokens == 60012


def test_a_missing_token_reads_as_identity_mismatch_not_transport():
    from experimental.analyst.backends.openai_api import OpenAICompatBackend

    class _Resp:
        status_code = 401
        headers = {"content-type": "application/json"}

    backend = OpenAICompatBackend(endpoint="http://127.0.0.1:9292")
    backend._read_all_status = lambda *a, **k: (b"{}", None)
    assert backend._classify_http_status(
        _Resp(), None, lambda: False, 0.0,
    ) is OllamaStatus.IDENTITY_MISMATCH
