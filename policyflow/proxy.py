"""Upstream proxy — forwards requests to vendor APIs directly.

Supports multi-provider routing with protocol awareness.  Providers with
``protocol: anthropic`` receive Anthropic Messages API requests (converted
from OpenAI format on the fly); all others receive standard OpenAI Chat
Completions.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger(__name__)


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
        self._sse_buffer: dict[str, str] = {}  # partial SSE event across TCP chunks

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

    def _chat_path(self, provider_name: str | None) -> str:
        """Build the chat-completions path for a provider."""
        if provider_name and self._is_anthropic(provider_name):
            return "/v1/messages"
        return self._openai_chat_path(provider_name)

    def _openai_chat_path(self, provider_name: str | None) -> str:
        """Build the OpenAI chat-completions path for a provider.

        If the provider's base_url already ends in an API version segment
        (…/v1, /v2, /v3, /v4), only append '/chat/completions' — otherwise
        the version would be duplicated (e.g. Volc Coding Plan's base_url is
        …/api/coding/v3, which 404s on '/v1/chat/completions').  Versionless
        base_urls (e.g. https://api.deepseek.com) get the '/v1' prefix.
        """
        import re
        if provider_name:
            base = self.config.get_provider_config(provider_name).get("base_url", "")
        else:
            base = self.config.upstream_base_url
        if re.search(r"/v\d+$", base.rstrip("/")):
            return "/chat/completions"
        return "/v1/chat/completions"

    def _is_anthropic(self, provider_name: str | None) -> bool:
        """Check if a provider uses the Anthropic Messages protocol."""
        if not provider_name:
            return False
        return self.config.get_provider_protocol(provider_name) == "anthropic"

    def _build_payload(
        self, request: ChatCompletionRequest, provider_name: str | None,
    ) -> dict:
        """Build the JSON payload for the upstream request.

        For Anthropic providers, converts OpenAI format to Anthropic Messages.
        For OpenAI providers, serializes as-is.
        """
        if self._is_anthropic(provider_name):
            from .anthropic_adapter import openai_to_anthropic_request
            return openai_to_anthropic_request(request)
        payload = request.model_dump(exclude_none=True, exclude={"extra"})
        # Merge extra fields, but skip private stashes (underscore-prefixed keys
        # like ``_anthropic_raw`` — those are internal adapter state, not for
        # upstream).  Forwarding them would bloat the payload, evict prompt
        # cache, and at worst trip strict-mode upstreams into 400 errors.
        for k, v in (request.extra or {}).items():
            if not k.startswith("_"):
                payload[k] = v
        return payload

    def _parse_anthropic_stream_chunk(self, chunk: bytes) -> list[bytes]:
        """Parse one Anthropic SSE chunk and convert to OpenAI SSE bytes.

        Uses ``self._sse_buffer`` to carry a partial event across TCP chunks
        — an ``event:`` and ``data:`` line landing in different chunks won't
        be silently dropped.
        """
        text = chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        result: list[bytes] = []
        buf = self._sse_buffer

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # Blank line — flush the current event if complete.
                if "event" in buf and "data" in buf:
                    evt = buf.pop("event")
                    data_str = buf.pop("data")
                    buf.clear()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if evt == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_content = delta.get("text", "")
                            oai_chunk = json.dumps({
                                "choices": [{"index": 0, "delta": {"content": text_content}}],
                            }, ensure_ascii=False)
                            result.append(f"data: {oai_chunk}\n\n".encode("utf-8"))
                    elif evt == "message_delta":
                        usage = data.get("usage", {})
                        finish = data.get("delta", {}).get("stop_reason", "end_turn")
                        oai_finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}.get(finish, "stop")
                        oai_chunk = json.dumps({
                            "choices": [{"index": 0, "delta": {}, "finish_reason": oai_finish}],
                            "usage": {
                                "prompt_tokens": usage.get("input_tokens", 0),
                                "completion_tokens": usage.get("output_tokens", 0),
                                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                            },
                        }, ensure_ascii=False)
                        result.append(f"data: {oai_chunk}\n\n".encode("utf-8"))
                    elif evt == "message_stop":
                        result.append(b"data: [DONE]\n\n")
                continue
            if stripped.startswith("event:"):
                buf["event"] = stripped[6:].strip()
            elif stripped.startswith("data:"):
                buf["data"] = stripped[5:].strip()

        return result

    @staticmethod
    def _parse_anthropic_response(body: dict) -> dict:
        """Convert an Anthropic Messages response to OpenAI ChatCompletion format."""
        content_blocks = body.get("content", [])
        text = ""
        tool_calls = []
        for block in content_blocks:
            if block.get("type") == "text":
                text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
        stop_reason = body.get("stop_reason", "end_turn")
        finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls"}.get(stop_reason, "stop")
        usage = body.get("usage", {})
        return {
            "id": body.get("id", ""),
            "object": "chat.completion",
            "model": body.get("model", ""),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text, "tool_calls": tool_calls or None},
                "finish_reason": finish,
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
        }

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
        """Forward a non-streaming chat completion request upstream.

        For Anthropic-protocol providers, converts the request to Anthropic
        Messages format and the response back to OpenAI ChatCompletion.
        """
        payload = self._build_payload(request, provider_name)
        response = await self._post(self._chat_path(provider_name), payload, provider_name)
        if self._is_anthropic(provider_name):
            data = self._parse_anthropic_response(response.json())
            return ChatCompletionResponse(**data)
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
        For Anthropic providers, converts Anthropic SSE → OpenAI SSE on the fly.
        """
        client = self._get_client(provider_name)
        label = self._provider_label(provider_name)
        payload = self._build_payload(request, provider_name)
        is_anthropic = self._is_anthropic(provider_name)

        try:
            async with client.stream("POST", self._chat_path(provider_name), json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ProxyError(
                        f"Upstream returned {response.status_code}: {body[:500]}",
                        status_code=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    if is_anthropic:
                        for oai_chunk in self._parse_anthropic_stream_chunk(chunk):
                            yield oai_chunk
                    else:
                        yield chunk
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream {label}", status_code=0)
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out: {label}", status_code=0)
        except httpx.StreamError as e:
            raise ProxyError(f"Stream error from upstream {label}: {e}", status_code=0)

    async def chat_completion_stream_with_fallback(
        self, request: ChatCompletionRequest,
    ) -> AsyncIterator[bytes]:
        """Streaming variant of ``chat_completion_with_fallback``.

        Tries every provider that declares this model; falls back to the
        upstream fallback_model when all providers are exhausted.
        """
        model = request.model
        candidates = self.config.get_model_providers(model) or [None]
        last_error: Exception | None = None

        for provider in candidates:
            try:
                async for chunk in self.chat_completion_stream(request, provider_name=provider):
                    yield chunk
                return
            except ProxyError as e:
                if e.retryable:
                    last_error = e
                    continue
                raise

        fallback = self.config.upstream_fallback_model
        logger = logging.getLogger(__name__)
        logger.warning("All streaming providers failed for %r, last: %s", model, last_error)
        if fallback:
            request.model = fallback
            async for chunk in self.chat_completion_stream(request):
                yield chunk

    async def anthropic_messages_stream(
        self, anthropic_body: dict, provider_name: str,
    ) -> AsyncIterator[bytes]:
        """Forward a raw Anthropic Messages request, yield raw SSE bytes.

        Used by /v1/messages when routing to an Anthropic provider,
        avoiding the OpenAI roundtrip entirely.
        """
        client = self._get_client(provider_name)
        label = self._provider_label(provider_name)
        try:
            async with client.stream("POST", "/v1/messages", json=anthropic_body) as response:
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

    async def anthropic_messages_stream_with_fallback(
        self, anthropic_body: dict, model: str,
    ) -> AsyncIterator[bytes]:
        """Streaming Anthropic Messages with provider fallback."""
        candidates = self.config.get_model_providers(model) or [None]
        last_error: Exception | None = None
        for provider in candidates:
            try:
                async for chunk in self.anthropic_messages_stream(anthropic_body, provider):
                    yield chunk
                return
            except ProxyError as e:
                if e.retryable:
                    last_error = e
                    continue
                raise
        raise ProxyError(
            f"All Anthropic providers failed for {model} (last: {last_error})",
            status_code=502,
        )

    async def anthropic_messages(
        self, anthropic_body: dict, provider_name: str,
    ) -> dict:
        """Forward a raw Anthropic Messages request, return JSON response.

        Used by /v1/messages when routing to an Anthropic provider.
        """
        client = self._get_client(provider_name)
        label = self._provider_label(provider_name)
        try:
            response = await client.post("/v1/messages", json=anthropic_body)
        except httpx.ConnectError:
            raise ProxyError(f"Cannot connect to upstream {label}", status_code=0)
        except httpx.TimeoutException:
            raise ProxyError(f"Upstream request timed out: {label}", status_code=0)
        if response.status_code != 200:
            raise ProxyError(
                f"Upstream returned {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json()

    async def anthropic_messages_with_fallback(
        self, anthropic_body: dict, model: str,
    ) -> dict:
        """Anthropic Messages with provider fallback."""
        candidates = self.config.get_model_providers(model) or [None]
        last_error: Exception | None = None
        for provider in candidates:
            try:
                return await self.anthropic_messages(anthropic_body, provider)
            except ProxyError as e:
                if e.retryable:
                    last_error = e
                    continue
                raise
        raise ProxyError(
            f"All Anthropic providers failed for {model} (last: {last_error})",
            status_code=502,
        )

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
