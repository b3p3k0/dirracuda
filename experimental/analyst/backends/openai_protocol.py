"""Pure parsing and request shaping for the OpenAI-compatible dialect.

No network, no database, no Tk. Every rule here traces to a measurement in
`PROBE_RESULTS.md` or to a live probe of llama.cpp build b1-f280b26 on
2026-09-23; nothing is taken from vendor documentation alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final

from .base import UNKNOWN_CONTEXT, BackendError

#: Terminator of a llama.cpp SSE stream. Measured.
SSE_DONE: Final = "[DONE]"
_SSE_PREFIX: Final = "data: "
_CTX_SIZE_ARG = re.compile(r"(?:^|\s)(?:--ctx-size|-c)[ =](\d+)")

MAX_FRAME_CHARS: Final = 512 * 1024
MAX_MODELS: Final = 256


@dataclass(frozen=True, slots=True)
class ServerModel:
    """One entry of a `/v1/models` catalogue."""

    model_id: str
    status: str | None
    args: tuple[str, ...]
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    meta: dict[str, Any] | None

    @property
    def is_loaded(self) -> bool:
        """Return whether the server currently holds this model in memory."""
        return self.status not in {"unloaded", "sleeping", None}

    @property
    def is_text_generation(self) -> bool:
        """Return whether this model can serve a chat run (contract 6.3).

        A router catalogue also carries embeddings-only and vision models. An
        embeddings server is launched `--embeddings`; a vision model carries
        `--mmproj`. Either would fail at runtime in a way the operator cannot
        diagnose, so neither is ever offered.
        """
        joined = " ".join(self.args)
        if "--embeddings" in joined or "--mmproj" in joined:
            return False
        if self.output_modalities and "text" not in self.output_modalities:
            return False
        if self.input_modalities and set(self.input_modalities) != {"text"}:
            return False
        return True


def parse_models(payload: object) -> tuple[ServerModel, ...]:
    """Parse a `/v1/models` body into bounded, content-free entries."""
    if type(payload) is not dict or type(payload.get("data")) is not list:
        raise BackendError("model catalogue is not an OpenAI model list")
    rows = payload["data"]
    if len(rows) > MAX_MODELS:
        raise BackendError("model catalogue exceeds its bound")
    models: list[ServerModel] = []
    for row in rows:
        if type(row) is not dict or type(row.get("id")) is not str or not row["id"]:
            raise BackendError("model catalogue row is malformed")
        status = row.get("status") if type(row.get("status")) is dict else {}
        arch = row.get("architecture") if type(row.get("architecture")) is dict else {}
        args = status.get("args")
        meta = row.get("meta") if type(row.get("meta")) is dict else None
        models.append(
            ServerModel(
                model_id=row["id"],
                status=status.get("value") if type(status.get("value")) is str else None,
                args=tuple(str(a) for a in args) if type(args) is list else (),
                input_modalities=_modalities(arch.get("input_modalities")),
                output_modalities=_modalities(arch.get("output_modalities")),
                meta=meta,
            )
        )
    return tuple(models)


def _modalities(value: object) -> tuple[str, ...]:
    if type(value) is not list:
        return ()
    return tuple(str(item) for item in value if type(item) is str)


def text_generation_models(
    models: tuple[ServerModel, ...],
) -> tuple[ServerModel, ...]:
    """Return only the models that can actually serve a chat run."""
    return tuple(model for model in models if model.is_text_generation)


def is_router(props: object) -> bool:
    """Return whether `/props` describes a router rather than one server.

    A router owns a catalogue and spawns a child per model. Measured: it
    reports `role: "router"`, `model_path: "none"`, and `n_ctx: 0`.
    """
    return type(props) is dict and props.get("role") == "router"


def _positive_int(value: object) -> int:
    return value if type(value) is int and value > 0 else UNKNOWN_CONTEXT


def context_from_args(args: tuple[str, ...]) -> int:
    """Return the `--ctx-size` a child server was launched with, or unknown."""
    match = _CTX_SIZE_ARG.search(" ".join(args))
    return int(match.group(1)) if match else UNKNOWN_CONTEXT


def discover_context(props: object, model: ServerModel | None) -> int:
    """Return the context one model will honour, or UNKNOWN_CONTEXT.

    Contract 5.3, exactly. Zero, absent or non-integer is **unknown**, never
    unlimited: reading a router's `n_ctx: 0` as a limit would refuse every run,
    and reading it as unlimited would approve every run.

    Deliberately does not divide by `total_slots`. That behaviour is
    version-dependent and did not occur on the probed build.
    """
    if is_router(props):
        if model is None:
            return UNKNOWN_CONTEXT
        if type(model.meta) is dict:
            found = _positive_int(model.meta.get("n_ctx"))
            if found:
                return found
        return context_from_args(model.args)
    if type(props) is not dict:
        return UNKNOWN_CONTEXT
    settings = props.get("default_generation_settings")
    if type(settings) is not dict:
        return UNKNOWN_CONTEXT
    return _positive_int(settings.get("n_ctx"))


def server_build(props: object) -> str | None:
    """Return the server build string, which is free provenance per response."""
    if type(props) is dict and type(props.get("build_info")) is str:
        return props["build_info"] or None
    return None


@dataclass(frozen=True, slots=True)
class ContextOverflow:
    """The server refused a prompt as larger than its context (contract 5.4)."""

    prompt_tokens: int
    context_tokens: int

    def message(self, model_id: str, host: str) -> str:
        """Return the operator-facing message, carrying both numbers."""
        return (
            f"Model {model_id} on {host} accepts {self.context_tokens} tokens. "
            f"This run needs {self.prompt_tokens}. Pick a model with a larger "
            f"context, or raise --ctx-size for this preset."
        )


def parse_error(body: bytes) -> tuple[str | None, ContextOverflow | None]:
    """Return an error type and, for a context overflow, both token counts.

    Measured shape:
    `{"error":{"code":400,"type":"exceed_context_size_error",
      "n_prompt_tokens":60012,"n_ctx":32768}}`
    """
    try:
        payload = json.loads(body.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, ValueError):
        return None, None
    if type(payload) is not dict or type(payload.get("error")) is not dict:
        return None, None
    error = payload["error"]
    kind = error.get("type") if type(error.get("type")) is str else None
    if kind != "exceed_context_size_error":
        return kind, None
    prompt = error.get("n_prompt_tokens")
    context = error.get("n_ctx")
    if type(prompt) is not int or type(context) is not int:
        return kind, None
    return kind, ContextOverflow(prompt_tokens=prompt, context_tokens=context)


@dataclass(frozen=True, slots=True)
class StreamOutcome:
    """One assembled streamed completion, with no reasoning text retained."""

    content: str
    finish_reason: str | None
    system_fingerprint: str | None
    prompt_tokens: int
    predicted_tokens: int
    prompt_ms: float
    predicted_ms: float
    #: True when the server produced only reasoning and no answer. Erratum E1
    #: and contract 7.4: the text itself is never retained, only the fact.
    reasoning_only: bool


def parse_sse_stream(lines: list[str]) -> StreamOutcome:
    """Assemble a llama.cpp SSE completion stream.

    Measured on build b1-f280b26: `data: {...}` frames terminated by
    `data: [DONE]`. Streaming carries no `usage` block; token counts live in
    `timings` on the final frame.
    """
    content: list[str] = []
    saw_reasoning = False
    finish_reason = None
    fingerprint = None
    timings: dict[str, Any] = {}
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith(_SSE_PREFIX):
            continue
        payload = line[len(_SSE_PREFIX):]
        if payload == SSE_DONE:
            break
        if len(payload) > MAX_FRAME_CHARS:
            raise BackendError("streamed frame exceeds its bound")
        try:
            frame = json.loads(payload)
        except ValueError:
            raise BackendError("streamed frame is not JSON") from None
        if type(frame) is not dict:
            raise BackendError("streamed frame is not an object")
        if type(frame.get("system_fingerprint")) is str:
            fingerprint = frame["system_fingerprint"]
        if type(frame.get("timings")) is dict:
            timings = frame["timings"]
        choices = frame.get("choices")
        if type(choices) is not list or not choices:
            continue
        choice = choices[0]
        if type(choice) is not dict:
            continue
        if type(choice.get("finish_reason")) is str:
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta")
        if type(delta) is not dict:
            continue
        piece = delta.get("content")
        if type(piece) is str:
            content.append(piece)
        # Never accumulated, never logged, never persisted (contract 7.4).
        if delta.get("reasoning_content"):
            saw_reasoning = True
    text = "".join(content)
    return StreamOutcome(
        content=text,
        finish_reason=finish_reason,
        system_fingerprint=fingerprint,
        prompt_tokens=_positive_int(timings.get("prompt_n")),
        predicted_tokens=_positive_int(timings.get("predicted_n")),
        prompt_ms=float(timings.get("prompt_ms") or 0.0),
        predicted_ms=float(timings.get("predicted_ms") or 0.0),
        reasoning_only=saw_reasoning and not text,
    )


def build_chat_body(
    ollama_payload: dict[str, Any], *, model_id: str, schema_name: str = "analyst",
) -> dict[str, Any]:
    """Translate one frozen Ollama chat payload into the OpenAI dialect.

    Contract 7.1 and 7.2: every sampling value is sent explicitly and nothing
    is inherited, because the probed server ships `temperature 1.0` and
    `top_p 0.95` as preset defaults.
    """
    options = ollama_payload.get("options")
    if type(options) is not dict:
        raise BackendError("chat payload carries no generation options")
    messages = ollama_payload.get("messages")
    if type(messages) is not list or not messages:
        raise BackendError("chat payload carries no messages")
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": True,
        "temperature": options["temperature"],
        "top_p": options["top_p"],
        "top_k": options["top_k"],
        "min_p": options["min_p"],
        "repeat_penalty": options["repeat_penalty"],
        "repeat_last_n": options["repeat_last_n"],
        "seed": options["seed"],
        "max_tokens": options["num_predict"],
        # Contract 7.4: the reasoning channel echoes the input verbatim and
        # eats the token budget. Disabled on every request, without exception.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    schema = ollama_payload.get("format")
    if type(schema) is dict:
        # Contract 7.5: measured working, so no GBNF fallback is contracted.
        body["response_format"] = {
            "type": "json_schema",
            "strict": True,
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        }
    return body


__all__ = [
    "MAX_FRAME_CHARS",
    "MAX_MODELS",
    "SSE_DONE",
    "ContextOverflow",
    "ServerModel",
    "StreamOutcome",
    "build_chat_body",
    "context_from_args",
    "discover_context",
    "is_router",
    "parse_error",
    "parse_models",
    "parse_sse_stream",
    "server_build",
    "text_generation_models",
]
