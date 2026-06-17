"""Upstream proxy — forwards requests to one-api or any OpenAI-compatible backend."""

from __future__ import annotations

from typing import AsyncIterator

import httpx

from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse


class ProxyError(Exception):
    """Raised when the upstream returns an error."""


class UpstreamProxy:
    """Async proxy that forwards chat completion requests upstream."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.upstream_base_url,
                timeout=httpx.Timeout(self.config.upstream_timeout),
                headers=self._build_headers(),
            )
        return self._client

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.upstream_api_key:
            headers["Authorization"] = f"Bearer {self.config.upstream_api_key}"
        return headers

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict) -> httpx.Response:
        """Send a POST request, catching connection errors."""
        client = await self._get_client()
        try:
            response = await client.post(path, json=payload)
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream at {self.config.upstream_base_url}")
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out after {self.config.upstream_timeout}s")
        if response.status_code != 200:
            raise ProxyError(
                f"Upstream returned {response.status_code}: {response.text[:500]}"
            )
        return response

    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """Forward a non-streaming chat completion request upstream."""
        payload = request.model_dump(exclude_none=True, exclude={"extra"})
        payload.update(request.extra or {})
        response = await self._post("/v1/chat/completions", payload)
        return ChatCompletionResponse(**response.json())

    async def chat_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[bytes]:
        """Forward a streaming chat completion request upstream.

        Yields raw SSE bytes from the upstream, one chunk at a time.
        """
        client = await self._get_client()
        payload = request.model_dump(exclude_none=True, exclude={"extra"})
        payload.update(request.extra or {})

        try:
            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ProxyError(
                        f"Upstream returned {response.status_code}: {body[:500]}"
                    )
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream at {self.config.upstream_base_url}")
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out after {self.config.upstream_timeout}s")

    async def list_models(self) -> dict:
        """Proxy the /v1/models endpoint."""
        client = await self._get_client()
        try:
            response = await client.get("/v1/models")
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream at {self.config.upstream_base_url}")
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out")
        if response.status_code != 200:
            raise ProxyError(
                f"Upstream returned {response.status_code}: {response.text[:500]}"
            )
        return response.json()
