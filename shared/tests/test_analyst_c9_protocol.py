"""Hostile offline tests for bounded Ollama response parsing."""

from __future__ import annotations

import hashlib
import json

import pytest

from experimental.analyst.ollama_contract import (
    MAX_BODY_BYTES,
    MAX_COMBINED_CHANNEL_BYTES,
    MAX_CONTENT_BYTES,
    MAX_FRAME_BYTES,
    MAX_JSON_DEPTH,
    MODEL_DIGEST,
    MODEL_TAG,
    QUALIFIED_OLLAMA_VERSION,
)
from experimental.analyst.ollama_protocol import (
    AnswerCode,
    ChatStreamParser,
    OllamaAnswerError,
    OllamaProvenanceError,
    OllamaSafetyError,
    OllamaStreamError,
    ProvenanceCode,
    SafetyCode,
    StreamCode,
    bound_json,
    canonical_json,
    parse_answer_json,
    parse_tags_response,
    parse_version_response,
    parse_wire_json,
)


def _encoded(value: object, *, newline: bool = True) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def _frame(
    content: str = "",
    *,
    thinking: str = "",
    done: bool = False,
    done_reason: str = "stop",
    **changes: object,
) -> dict[str, object]:
    frame: dict[str, object] = {
        "model": MODEL_TAG,
        "message": {"role": "assistant", "content": content, "thinking": thinking},
        "done": done,
    }
    if done:
        frame.update({
            "done_reason": done_reason,
            "total_duration": 100,
            "load_duration": 10,
            "prompt_eval_count": 5,
            "prompt_eval_duration": 20,
            "eval_count": 3,
            "eval_duration": 30,
        })
    frame.update(changes)
    return frame


def _parse(*frames: dict[str, object]):
    parser = ChatStreamParser(MODEL_TAG)
    for frame in frames:
        parser.feed(_encoded(frame))
    return parser.finish()


def test_version_response_accepts_only_exact_single_field_identity() -> None:
    result = parse_version_response(
        _encoded({"version": QUALIFIED_OLLAMA_VERSION}, newline=False),
        QUALIFIED_OLLAMA_VERSION,
    )
    assert result.version == QUALIFIED_OLLAMA_VERSION

    for value in (
        {"version": "0.32.6"},
        {"version": QUALIFIED_OLLAMA_VERSION, "extra": None},
        {"version": 0.325},
        [],
    ):
        with pytest.raises(OllamaProvenanceError) as caught:
            parse_version_response(_encoded(value, newline=False), QUALIFIED_OLLAMA_VERSION)
        assert caught.value.code is ProvenanceCode.VERSION_MISMATCH


@pytest.mark.parametrize("expected", ["", None, True])
def test_version_response_rejects_invalid_expectation(expected: object) -> None:
    with pytest.raises(OllamaProvenanceError) as caught:
        parse_version_response(b'{"version":"0.32.5"}', expected)  # type: ignore[arg-type]
    assert caught.value.code is ProvenanceCode.INVALID_EXPECTATION


def test_tags_response_retains_only_sorted_approved_identity() -> None:
    payload = {"models": [
        {"name": "unrelated:latest", "model": "unrelated:latest", "digest": "1" * 64,
         "size": 42, "remote_host": ""},
        {"name": MODEL_TAG, "model": MODEL_TAG, "digest": MODEL_DIGEST,
         "details": {"family": "qwen"}},
    ]}
    result = parse_tags_response(
        _encoded(payload, newline=False), {MODEL_TAG: MODEL_DIGEST},
    )
    assert [(item.model, item.digest) for item in result.models] == [
        (MODEL_TAG, MODEL_DIGEST),
    ]
    assert "unrelated" not in repr(result)


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, ProvenanceCode.TAGS_SHAPE),
        ({"models": {}}, ProvenanceCode.TAGS_SHAPE),
        ({"models": ["row"]}, ProvenanceCode.TAGS_ROW_SHAPE),
        ({"models": [{"name": MODEL_TAG, "model": "alias", "digest": MODEL_DIGEST}]},
         ProvenanceCode.TAGS_NAME_MISMATCH),
        ({"models": [
            {"name": MODEL_TAG, "model": MODEL_TAG, "digest": MODEL_DIGEST},
            {"name": MODEL_TAG, "model": MODEL_TAG, "digest": MODEL_DIGEST},
        ]}, ProvenanceCode.DUPLICATE_MODEL_TAG),
        ({"models": [{"name": MODEL_TAG, "model": MODEL_TAG, "digest": "x"}]},
         ProvenanceCode.INVALID_MODEL_DIGEST),
        ({"models": []}, ProvenanceCode.MODEL_MISSING),
        ({"models": [{"name": MODEL_TAG, "model": MODEL_TAG, "digest": "1" * 64}]},
         ProvenanceCode.MODEL_DIGEST_MISMATCH),
    ],
)
def test_tags_response_rejects_hostile_identity_shapes(
    payload: object, code: ProvenanceCode,
) -> None:
    with pytest.raises(OllamaProvenanceError) as caught:
        parse_tags_response(
            _encoded(payload, newline=False), {MODEL_TAG: MODEL_DIGEST},
        )
    assert caught.value.code is code


@pytest.mark.parametrize("tag", ["qwen3.6:cloud", "qwen3.6-cloud", "QWEN-CLOUD:27b"])
def test_tags_response_rejects_known_cloud_expected_tags(tag: str) -> None:
    payload = {"models": [{"name": tag, "model": tag, "digest": MODEL_DIGEST}]}
    with pytest.raises(OllamaProvenanceError) as caught:
        parse_tags_response(_encoded(payload, newline=False), {tag: MODEL_DIGEST})
    assert caught.value.code is ProvenanceCode.CLOUD_MODEL_REFUSED


@pytest.mark.parametrize(
    "expected",
    [{}, {MODEL_TAG: "x"}, {"": MODEL_DIGEST}, None],
)
def test_tags_response_rejects_invalid_expected_mapping(expected: object) -> None:
    with pytest.raises(OllamaProvenanceError) as caught:
        parse_tags_response(b'{"models":[]}', expected)  # type: ignore[arg-type]
    assert caught.value.code is ProvenanceCode.INVALID_EXPECTATION


def test_wire_json_accepts_body_limit_n_but_rejects_n_plus_one() -> None:
    with pytest.raises(OllamaSafetyError) as at_limit:
        parse_wire_json(b" " * MAX_BODY_BYTES)
    assert at_limit.value.code is SafetyCode.INVALID_WIRE_JSON

    with pytest.raises(OllamaSafetyError) as over_limit:
        parse_wire_json(b" " * (MAX_BODY_BYTES + 1))
    assert over_limit.value.code is SafetyCode.BODY_LIMIT


@pytest.mark.parametrize("raw", [b"\xff", b'{"x":1,"x":2}', b'{"x":NaN}', b"["])
def test_wire_json_rejects_invalid_utf8_duplicates_nonfinite_and_truncation(
    raw: bytes,
) -> None:
    with pytest.raises(OllamaSafetyError) as caught:
        parse_wire_json(raw)
    assert caught.value.code is SafetyCode.INVALID_WIRE_JSON


def test_json_node_and_depth_limits_have_exact_boundaries() -> None:
    bound_json([1], max_nodes=2)
    with pytest.raises(OllamaSafetyError) as nodes:
        bound_json([1, 2], max_nodes=2)
    assert nodes.value.code is SafetyCode.JSON_NODE_LIMIT

    value: object = 0
    for _ in range(MAX_JSON_DEPTH - 1):
        value = [value]
    bound_json(value)
    value = [value]
    with pytest.raises(OllamaSafetyError) as depth:
        bound_json(value)
    assert depth.value.code is SafetyCode.JSON_DEPTH_LIMIT


def test_answer_json_distinguishes_model_invalid_from_safety_limit() -> None:
    assert parse_answer_json('{"ok":true}') == {"ok": True}
    for raw in ('{"x":1,"x":2}', '{"x":NaN}', "not json"):
        with pytest.raises(OllamaAnswerError) as caught:
            parse_answer_json(raw)
        assert caught.value.code is AnswerCode.INVALID_JSON

    too_deep: object = 0
    for _ in range(MAX_JSON_DEPTH):
        too_deep = [too_deep]
    with pytest.raises(OllamaSafetyError) as caught:
        parse_answer_json(json.dumps(too_deep))
    assert caught.value.code is SafetyCode.JSON_DEPTH_LIMIT


def test_chat_parser_accepts_fragmented_multibyte_and_unterminated_final_frame() -> None:
    first = _encoded(_frame("café", thinking="private-thought"))
    final = _encoded(_frame("!", done=True), newline=False)
    wire = first + final
    marker = wire.index("é".encode("utf-8")) + 1
    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(wire[:marker])
    assert parser.content_started is False
    parser.feed(wire[marker:])
    result = parser.finish()

    assert result.model == MODEL_TAG
    assert result.content == "café!"
    assert result.content_sha256 == hashlib.sha256("café!".encode()).hexdigest()
    assert result.metrics.done_reason == "stop"
    assert result.metrics.content_bytes == len("café!".encode())
    assert result.metrics.thinking_bytes == len("private-thought".encode())
    assert result.metrics.raw_body_bytes == len(wire)
    assert "private-thought" not in repr(result.metrics)


def test_parsed_chat_response_repr_never_contains_model_content() -> None:
    marker = "PUBLIC_MODEL_OUTPUT_MUST_NOT_ENTER_REPR"
    result = _parse(_frame(marker, done=True))
    assert result.content == marker
    assert marker not in repr(result)


def test_chat_parser_accepts_blank_lines_and_exact_length_terminal() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(b"\n \r\n" + _encoded(_frame("{}", done=True, done_reason="length")))
    result = parser.finish()
    assert result.content == "{}"
    assert result.metrics.done_reason == "length"


def test_frame_limit_accepts_n_pending_and_rejects_n_plus_one() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(b"x" * MAX_FRAME_BYTES)
    assert parser.body_bytes == MAX_FRAME_BYTES
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(b"x")
    assert caught.value.code is SafetyCode.FRAME_LIMIT


def test_content_limit_accepts_n_and_rejects_n_plus_one() -> None:
    at_limit = _parse(
        _frame("x" * MAX_CONTENT_BYTES),
        _frame(done=True),
    )
    assert at_limit.metrics.content_bytes == MAX_CONTENT_BYTES

    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(_encoded(_frame("x" * MAX_CONTENT_BYTES)))
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(_encoded(_frame("x")))
    assert caught.value.code is SafetyCode.CONTENT_LIMIT


def test_combined_channel_limit_accepts_n_and_rejects_n_plus_one() -> None:
    piece = "t" * (MAX_FRAME_BYTES // 2)
    remaining = MAX_COMBINED_CHANNEL_BYTES
    parser = ChatStreamParser(MODEL_TAG)
    while remaining:
        current = piece[:remaining]
        parser.feed(_encoded(_frame(thinking=current)))
        remaining -= len(current)
    parser.feed(_encoded(_frame(done=True)))
    assert parser.finish().metrics.thinking_bytes == MAX_COMBINED_CHANNEL_BYTES

    parser = ChatStreamParser(MODEL_TAG)
    remaining = MAX_COMBINED_CHANNEL_BYTES
    while remaining:
        current = piece[:remaining]
        parser.feed(_encoded(_frame(thinking=current)))
        remaining -= len(current)
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(_encoded(_frame(thinking="x")))
    assert caught.value.code is SafetyCode.COMBINED_CHANNEL_LIMIT


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"model": "other"}, ProvenanceCode.RESPONSE_MODEL_MISMATCH),
        ({"done": 1}, SafetyCode.INVALID_STREAM_FRAME),
        ({"created_at": 1}, SafetyCode.INVALID_STREAM_TIMESTAMP),
        ({"remote_host": "https://cloud.invalid"}, SafetyCode.UNKNOWN_TOP_LEVEL_CHANNEL),
        ({"logprobs": [1]}, SafetyCode.FORBIDDEN_LOGPROBS_CHANNEL),
        ({"prompt_eval_count": True}, SafetyCode.INVALID_METRIC_TYPE),
        ({"eval_duration": -1}, SafetyCode.INVALID_METRIC_TYPE),
    ],
)
def test_terminal_frame_rejects_identity_channels_and_metric_types(
    changes: dict[str, object], code: object,
) -> None:
    frame = _frame("{}", done=True)
    frame.update(changes)
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises((OllamaSafetyError, OllamaProvenanceError)) as caught:
        parser.feed(_encoded(frame))
    assert caught.value.code is code


@pytest.mark.parametrize(
    ("message_changes", "code"),
    [
        ({"role": "user"}, SafetyCode.INVALID_MESSAGE_ROLE),
        ({"content": 1}, SafetyCode.INVALID_TEXT_CHANNEL),
        ({"thinking": 1}, SafetyCode.INVALID_TEXT_CHANNEL),
        ({"tool_calls": [{"function": "danger"}]}, SafetyCode.FORBIDDEN_MESSAGE_CHANNEL),
        ({"images": ["raw"]}, SafetyCode.FORBIDDEN_MESSAGE_CHANNEL),
        ({"remote_host": "cloud"}, SafetyCode.UNKNOWN_MESSAGE_CHANNEL),
    ],
)
def test_message_rejects_forbidden_or_unknown_nonempty_channels(
    message_changes: dict[str, object], code: SafetyCode,
) -> None:
    frame = _frame("{}", done=True)
    frame["message"] = {**frame["message"], **message_changes}  # type: ignore[dict-item]
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(_encoded(frame))
    assert caught.value.code is code


def test_empty_unknown_channels_are_tolerated_but_never_retained() -> None:
    frame = _frame("{}", done=True, remote_model="")
    frame["message"] = {**frame["message"], "tool_name": None}  # type: ignore[dict-item]
    result = _parse(frame)
    assert result.content == "{}"
    assert "remote_model" not in repr(result)


@pytest.mark.parametrize("reason", ["", "done", "cancelled", None, True])
def test_done_reason_is_closed_to_stop_or_length(reason: object) -> None:
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(_encoded(_frame("{}", done=True, done_reason=reason)))  # type: ignore[arg-type]
    assert caught.value.code is SafetyCode.INVALID_DONE_REASON


def test_terminal_frame_requires_every_metric() -> None:
    for missing in (
        "total_duration", "load_duration", "prompt_eval_count",
        "prompt_eval_duration", "eval_count", "eval_duration",
    ):
        frame = _frame("{}", done=True)
        del frame[missing]
        parser = ChatStreamParser(MODEL_TAG)
        with pytest.raises(OllamaSafetyError) as caught:
            parser.feed(_encoded(frame))
        assert caught.value.code is SafetyCode.INVALID_METRIC_TYPE


def test_frame_after_done_and_double_finish_are_rejected() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaSafetyError) as after:
        parser.feed(_encoded(_frame(done=True)) + _encoded(_frame(done=True)))
    assert after.value.code is SafetyCode.FRAME_AFTER_DONE

    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(_encoded(_frame(done=True)))
    parser.finish()
    with pytest.raises(OllamaStreamError) as finished:
        parser.finish()
    assert finished.value.code is StreamCode.ALREADY_FINISHED


def test_truncated_stream_and_nonobject_frame_are_rejected() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    parser.feed(_encoded(_frame("partial")))
    with pytest.raises(OllamaStreamError) as ended:
        parser.finish()
    assert ended.value.code is StreamCode.ENDED_WITHOUT_DONE

    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaSafetyError) as nonobject:
        parser.feed(b"[]\n")
    assert nonobject.value.code is SafetyCode.FRAME_NOT_OBJECT


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("CUDA out of memory", StreamCode.RESOURCE_ERROR),
        ("resource exhausted", StreamCode.RESOURCE_ERROR),
        ("model runner failed", StreamCode.STREAM_ERROR),
        (None, StreamCode.STREAM_ERROR),
    ],
)
def test_midstream_error_classification_is_closed_and_content_free(
    message: object, code: StreamCode,
) -> None:
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaStreamError) as caught:
        parser.feed(_encoded({"error": message}))
    assert caught.value.code is code
    assert str(caught.value) == code.value
    assert "CUDA" not in str(caught.value)


@pytest.mark.parametrize("raw", [b"\xff\n", b'{"x":1,"x":2}\n', b'{"x":NaN}\n'])
def test_stream_rejects_invalid_utf8_duplicate_keys_and_nonfinite(raw: bytes) -> None:
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(OllamaSafetyError) as caught:
        parser.feed(raw)
    assert caught.value.code is SafetyCode.INVALID_WIRE_JSON


def test_protocol_canonical_json_rejects_unrepresentable_value() -> None:
    with pytest.raises(OllamaSafetyError) as caught:
        canonical_json({"x": object()})
    assert caught.value.code is SafetyCode.UNCANONICALIZABLE_JSON


def test_before_frame_callback_runs_before_each_coalesced_frame_and_can_stop_decode() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    calls = 0

    class StopBeforeSecond(RuntimeError):
        pass

    def before_frame() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StopBeforeSecond

    wire = _encoded(_frame("first")) + b"not-json\n"
    with pytest.raises(StopBeforeSecond):
        parser.feed(wire, before_frame=before_frame)
    assert calls == 2
    assert parser.content_started is True


def test_before_frame_callback_is_not_called_for_blank_frames() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    calls: list[str] = []
    parser.feed(
        b"\n \r\n" + _encoded(_frame(done=True)),
        before_frame=lambda: calls.append("frame"),
    )
    parser.finish()
    assert calls == ["frame"]


def test_before_frame_callback_must_be_callable() -> None:
    parser = ChatStreamParser(MODEL_TAG)
    with pytest.raises(TypeError, match="before_frame"):
        parser.feed(b"", before_frame=True)  # type: ignore[arg-type]
