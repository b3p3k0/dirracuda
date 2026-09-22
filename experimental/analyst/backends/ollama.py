"""Native Ollama backend.

A thin wrapper over the existing, benchmarked ``OllamaClient``. The client is
not rewritten: contract section 3 keeps ``/api/chat`` with per-request
``num_ctx``, ``top_k``, ``min_p``, ``repeat_penalty``, ``keep_alive`` and
tag+digest identity exactly as it was through C0B-7.
"""

from __future__ import annotations

from typing import Any

from ..endpoint import DEFAULT_ENDPOINT, Endpoint
from ..ollama_client import OllamaClient
from .base import BackendKind, IdentityKind, ModelIdentity


class OllamaBackend:
    """The native Ollama transport, presented through the backend surface."""

    kind = BackendKind.OLLAMA

    def __init__(
        self,
        *,
        endpoint: str | Endpoint = DEFAULT_ENDPOINT,
        session: Any | None = None,
        client: Any | None = None,
    ) -> None:
        self._client = (
            client
            if client is not None
            else OllamaClient(endpoint=endpoint, session=session)
        )

    @property
    def client(self) -> Any:
        """Return the underlying client, for callers that inject one."""
        return self._client

    @property
    def endpoint(self) -> Endpoint:
        """Return the endpoint this backend is bound to."""
        return self._client.endpoint

    # The run path calls these; they are forwarded unchanged so the Ollama
    # behaviour stays byte-identical.
    def preflight(self, *args: Any, **kwargs: Any):
        return self._client.preflight(*args, **kwargs)

    def check_version(self, *args: Any, **kwargs: Any):
        return self._client.check_version(*args, **kwargs)

    def check_tags(self, *args: Any, **kwargs: Any):
        return self._client.check_tags(*args, **kwargs)

    def list_models(self, *args: Any, **kwargs: Any):
        return self._client.list_models(*args, **kwargs)

    def chat(self, *args: Any, **kwargs: Any):
        return self._client.chat(*args, **kwargs)

    def cancel_current(self) -> None:
        self._client.cancel_current()

    @staticmethod
    def identity_for(model_tag: str, model_digest: str) -> ModelIdentity:
        """Return the verified identity an Ollama tag+digest establishes."""
        return ModelIdentity(
            kind=IdentityKind.DIGEST,
            model_name=model_tag,
            digest=model_digest,
        )


__all__ = ["OllamaBackend"]
