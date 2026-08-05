"""
Measurement-scoped Ollama client: streaming, cancellable, bounded.

DISPOSITION: ported to production in C9.

Why streaming (CONTRACT.md §8): a threading.Event cannot interrupt a blocking
`stream:false` read. Cancellation needs `stream:true` plus a cancel-checked read
loop plus socket deadlines. /api/ps does not prove a request stopped, so an
unproven server stop is reported as such and never as "cancelled".

The `thinking` field (erratum E1, gpt-oss) is sensitive model output: it counts
against the byte budget, and it is never written to an operational log.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from . import preflight, worksheet

MAX_RESPONSE_BYTES = 1 << 20        # 1 MiB, hard cap, includes thinking
READ_TIMEOUT_S = 120.0
CONNECT_TIMEOUT_S = 10.0


@dataclass
class GenOptions:
    """Every generation option, explicit. Nothing is left to a model default."""
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = 1
    min_p: float = 0.0
    repeat_penalty: float = 1.0
    repeat_last_n: int = 0
    seed: int = 1
    num_ctx: int = 8192
    # 2048, not 1024: measured 2026-08-04, gpt-oss:20b spends its whole
    # output budget on the reasoning trace and never reaches the answer
    # channel at 1024 (done_reason=length, content empty). At 2048 it
    # emits a 3416-byte trace plus a 273-byte answer and stops cleanly.
    num_predict: int = 2048

    def as_dict(self) -> Dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "repeat_last_n": self.repeat_last_n,
            "seed": self.seed,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }


@dataclass
class CallResult:
    ok: bool
    model: str
    outcome: str                     # ok | truncated | over_budget | cancelled
                                     # | transport_error | http_error | timeout
    content: str = ""
    thinking_bytes: int = 0
    content_bytes: int = 0
    total_bytes: int = 0
    done_reason: Optional[str] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
    load_duration_ns: Optional[int] = None
    prompt_eval_duration_ns: Optional[int] = None
    eval_duration_ns: Optional[int] = None
    total_duration_ns: Optional[int] = None
    wall_seconds: float = 0.0
    http_status: Optional[int] = None
    error: Optional[str] = None
    envelope: Dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> Dict[str, Any]:
        """Log-safe view: counts and timings only. No prompt, no content, no
        thinking, no identifiers (CONTRACT.md §11 content-free logs)."""
        return {
            "model": self.model, "outcome": self.outcome, "ok": self.ok,
            "done_reason": self.done_reason,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "content_bytes": self.content_bytes,
            "thinking_bytes": self.thinking_bytes,
            "wall_seconds": round(self.wall_seconds, 3),
            "http_status": self.http_status,
            "error": self.error,
        }


def headroom_ok(prompt_tokens: int, num_predict: int, num_ctx: int) -> bool:
    """BENCHMARK_PROTOCOL_C0B1.md §4, exactly:
    prompt_tokens + num_predict <= floor(0.85 * num_ctx)"""
    return prompt_tokens + num_predict <= int(0.85 * num_ctx)


class OllamaClient:
    """One in-flight request at a time. The benchmark is strictly serial."""

    def __init__(self, endpoint: str = preflight.DEFAULT_ENDPOINT,
                 *, keep_alive: str = "15m") -> None:
        import requests
        self.endpoint = endpoint
        self.keep_alive = keep_alive        # explicit short positive, never 0
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.max_redirects = 0
        self._lock = threading.Lock()

    def generate(self, model: str, prompt: str, ws_version: str,
                 opts: GenOptions, *,
                 cancel: Optional[threading.Event] = None,
                 total_deadline_s: Optional[float] = None,
                 on_first_token: Optional[Callable[[], None]] = None
                 ) -> CallResult:
        # /api/chat, not /api/generate.
        #
        # Measured 2026-08-04: on /api/generate with `format` set, gpt-oss:20b
        # returns done_reason=stop with an EMPTY response and no `thinking`
        # field - the evaluated tokens are unreachable. The same request on
        # /api/chat returns both `message.thinking` and `message.content`.
        # Qwen behaves identically on either endpoint, so /api/chat is the one
        # endpoint that serves every candidate.
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "format": worksheet.json_schema(ws_version),
            "options": opts.as_dict(),
            "think": preflight.think_value(model),
            "keep_alive": self.keep_alive,
        }
        res = CallResult(ok=False, model=model, outcome="transport_error")
        started = time.monotonic()
        chunks: list[str] = []
        thinking_bytes = 0
        content_bytes = 0
        first = True

        with self._lock:                       # enforce single in-flight request
            try:
                r = self._session.post(
                    f"{self.endpoint}/api/chat", json=payload, stream=True,
                    timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                    allow_redirects=False, proxies={"http": None, "https": None})
                res.http_status = r.status_code
                if r.status_code != 200:
                    res.outcome = "http_error"
                    res.error = f"HTTP {r.status_code}"
                    r.close()
                    res.wall_seconds = time.monotonic() - started
                    return res

                for line in r.iter_lines(decode_unicode=False):
                    if cancel is not None and cancel.is_set():
                        r.close()
                        res.outcome = "cancelled"
                        break
                    if total_deadline_s is not None and \
                            time.monotonic() - started > total_deadline_s:
                        r.close()
                        res.outcome = "timeout"
                        res.error = "total-run deadline exceeded"
                        break
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue

                    msg = obj.get("message") or {}
                    piece = msg.get("content") or ""
                    think = msg.get("thinking") or ""
                    if piece and first:
                        first = False
                        if on_first_token:
                            on_first_token()
                    content_bytes += len(piece.encode("utf-8"))
                    thinking_bytes += len(think.encode("utf-8"))
                    if content_bytes + thinking_bytes > MAX_RESPONSE_BYTES:
                        r.close()
                        res.outcome = "over_budget"
                        res.error = "response byte cap exceeded"
                        break
                    if piece:
                        chunks.append(piece)

                    if obj.get("done"):
                        res.done_reason = obj.get("done_reason")
                        res.prompt_eval_count = obj.get("prompt_eval_count")
                        res.eval_count = obj.get("eval_count")
                        res.load_duration_ns = obj.get("load_duration")
                        res.prompt_eval_duration_ns = obj.get("prompt_eval_duration")
                        res.eval_duration_ns = obj.get("eval_duration")
                        res.total_duration_ns = obj.get("total_duration")
                        res.outcome = ("truncated"
                                       if res.done_reason == "length" else "ok")
                        res.ok = res.outcome == "ok"
                        break
                else:
                    if res.outcome == "transport_error":
                        res.outcome = "transport_error"
                        res.error = "stream ended without done"
                r.close()
            except Exception as exc:                # noqa: BLE001
                res.outcome = "transport_error"
                res.error = type(exc).__name__

        res.content = "".join(chunks)
        res.content_bytes = content_bytes
        res.thinking_bytes = thinking_bytes
        res.total_bytes = content_bytes + thinking_bytes
        res.wall_seconds = time.monotonic() - started
        return res

    def show(self, model: str, timeout: float = 20.0) -> Dict[str, Any]:
        """/api/show metadata so effective parameters are provable, not assumed."""
        r = self._session.post(f"{self.endpoint}/api/show", json={"model": model},
                               timeout=timeout, allow_redirects=False,
                               proxies={"http": None, "https": None})
        r.raise_for_status()
        return r.json()

    def ps(self, timeout: float = 10.0) -> Dict[str, Any]:
        r = self._session.get(f"{self.endpoint}/api/ps", timeout=timeout,
                              allow_redirects=False,
                              proxies={"http": None, "https": None})
        r.raise_for_status()
        return r.json()
