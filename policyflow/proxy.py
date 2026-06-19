"""Upstream proxy — forwards requests to vendor APIs directly.

Supports multi-provider routing: each provider (DeepSeek, Anthropic, OpenAI, etc.)
has its own httpx client with its own base_url and api_key.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse


class ProxyError(Exception):
    """Raised when the upstream returns an error."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        """Whether this error is worth retrying on another provider.

        Transient errors (quota, rate-limit, server-down, connection) are
        retryable.  Permanent errors (bad request, auth) are not — there is
        no point trying another provider with the same broken payload.
        """
        return self.status_code in (401, 402, 429, 500, 502, 503, 504) or self.status_code == 0


class UpstreamProxy:
    """Async proxy that forwards chat completion requests upstream.

    Maintains a pool of httpx clients, one per provider, created lazily.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _get_client(self, provider_name: str | None = None) -> httpx.AsyncClient:
        """Get or lazily create an httpx client for the given provider.

        When provider_name is None, uses the default upstream config.
        """
        key = provider_name or "__default__"
        if key not in self._clients:
            if provider_name:
                cfg = self.config.get_provider_config(provider_name)
            else:
                cfg = {
                    "base_url": self.config.upstream_base_url,
                    "api_key": self.config.upstream_api_key,
                    "timeout": self.config.upstream_timeout,
                }
            headers = {"Content-Type": "application/json"}
            if cfg["api_key"]:
                headers["Authorization"] = f"Bearer {cfg['api_key']}"
            self._clients[key] = httpx.AsyncClient(
                base_url=cfg["base_url"],
                timeout=httpx.Timeout(cfg["timeout"]),
                headers=headers,
            )
        return self._clients[key]

    def _provider_label(self, provider_name: str | None) -> str:
        """Human-readable label for error messages."""
        if provider_name:
            cfg = self.config.get_provider_config(provider_name)
            return f"{provider_name} ({cfg['base_url']})"
        return f"default ({self.config.upstream_base_url})"

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()

    async def _post(
        self, path: str, payload: dict, provider_name: str | None = None,
    ) -> httpx.Response:
        """Send a POST request, catching connection errors."""
        client = self._get_client(provider_name)
        label = self._provider_label(provider_name)
        try:
            response = await client.post(path, json=payload)
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream {label}", status_code=0)
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out: {label}", status_code=0)
        except httpx.StreamError as e:
            raise ProxyError(f"Stream error from upstream {label}: {e}", status_code=0)
        if response.status_code != 200:
            raise ProxyError(
                f"Upstream returned {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response

    async def chat_completion(
        self, request: ChatCompletionRequest, provider_name: str | None = None,
    ) -> ChatCompletionResponse:
        """Forward a non-streaming chat completion request upstream."""
        payload = request.model_dump(exclude_none=True, exclude={"extra"})
        payload.update(request.extra or {})
        response = await self._post("/v1/chat/completions", payload, provider_name)
        return ChatCompletionResponse(**response.json())

    async def chat_completion_with_fallback(
        self, request: ChatCompletionRequest,
    ) -> tuple[ChatCompletionResponse, str]:
        """Forward with automatic provider fallback on transient errors.

        Tries every provider that declares this model, in yaml-defined order.
        Retryable errors (quota/rate-limit/server-down/connection) cause a
        silent jump to the next provider.  Permanent errors re-raise immediately.

        Returns (response, provider_name_used).
        """
        model = request.model
        candidates = self.config.get_model_providers(model)
        last_error: Exception | None = None

        for provider in candidates:
            try:
                resp = await self.chat_completion(request, provider_name=provider)
                return resp, provider
            except ProxyError as e:
                if e.retryable:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Provider %r failed for model %r (retryable), trying next: %s",
                        provider, model, e,
                    )
                    last_error = e
                    continue
                raise
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Provider %r unreachable for model %r, trying next: %s",
                    provider, model, e,
                )
                last_error = e
                continue

        # All providers exhausted (or none declared). Fall back to upstream.
        fallback = self.config.upstream_fallback_model
        logger = logging.getLogger(__name__)
        if not candidates:
            logger.warning("Model %r not in any provider", model)
        else:
            logger.warning(
                "All providers failed for model %r (last: %s)", model, last_error,
            )
        if fallback:
            logger.warning("Rewriting %r → %r and forwarding to upstream", model, fallback)
            request.model = fallback
        return await self.chat_completion(request), "upstream"

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_name: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Forward a streaming chat completion request upstream.

        Yields raw SSE bytes from the upstream, one chunk at a time.
        """
        client = self._get_client(provider_name)
        label = self._provider_label(provider_name)
        payload = request.model_dump(exclude_none=True, exclude={"extra"})
        payload.update(request.extra or {})

        try:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ProxyError(
                        f"Upstream returned {response.status_code}: {body[:500]}",
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream {label}", status_code=0)
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out: {label}", status_code=0)
        except httpx.StreamError as e:
            raise ProxyError(f"Stream error from upstream {label}: {e}", status_code=0)

    async def list_models(self) -> dict:
        """Proxy the /v1/models endpoint (uses default upstream)."""
        client = self._get_client(None)
        try:
            response = await client.get("/v1/models")
        except httpx.ConnectError:
            raise ProxyError(
                f"Cannot connect to upstream at {self.config.upstream_base_url}"
            )
        except httpx.TimeoutException:
            raise ProxyError("Upstream request timed out")
        if response.status_code != 200:
            raise ProxyError(
                f"Upstream returned {response.status_code}: {response.text[:500]}"
            )
        return response.json()
