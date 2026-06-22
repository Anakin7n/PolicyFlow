"""Anthropic Messages API <-> OpenAI Chat Completions adapter.

Converts requests and responses so Claude Code (Anthropic-native client) can
talk to PolicyFlow's OpenAI-based routing pipeline transparently.

Protocols handled:
  - Anthropic Messages:  POST /v1/messages  (request + response + streaming)
  - OpenAI Chat Comps:   POST /v1/chat/completions
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

from .models import ChatCompletionRequest, Message as PFMessage

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Request conversion: Anthropic Messages → OpenAI Chat Completions
# ═════════════════════════════════════════════════════════════════════

def anthropic_to_chat_request(data: dict[str, Any]) -> ChatCompletionRequest:
    """Convert an Anthropic Messages API request body to a ChatCompletionRequest.

    The returned request goes straight into the existing PolicyFlow pipeline
    (router -> proxy) — no other code changes needed.
    """
    messages = _convert_messages(data)
    tools = _convert_tools(data.get("tools"))

    return ChatCompletionRequest(
        model=data.get("model", "claude-sonnet-4-6"),
        messages=messages,
        stream=data.get("stream", False),
        temperature=data.get("temperature", 0.7),
        top_p=data.get("top_p", 1.0),
        max_tokens=data.get("max_tokens", 1024),
        stop=data.get("stop_sequences"),
        user=data.get("metadata", {}).get("user_id") if isinstance(data.get("metadata"), dict) else None,
        extra={"tools": tools} if tools else {},
    )


def _convert_messages(data: dict[str, Any]) -> list[PFMessage]:
    """Convert Anthropic messages array to OpenAI-format Message list."""
    result: list[PFMessage] = []

    # Anthropic's system is a top-level field; OpenAI prepends it as role="system"
    system = data.get("system")
    if system:
        system_text = _extract_system_text(system)
        if system_text:
            result.append(PFMessage(role="system", content=system_text))

    for msg in data.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content")
        if content is None:
            continue

        if isinstance(content, str):
            result.append(PFMessage(role=role, content=content))
        elif isinstance(content, list):
            if role == "assistant":
                result.append(_convert_assistant_blocks(content))
            else:
                result.extend(_convert_user_blocks(content))

    return result


def _extract_system_text(system: str | list[dict]) -> str:
    """Anthropic system field: string or [{type:"text", text:"..."}, ...]."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return " ".join(
            b.get("text", "") for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _convert_user_blocks(blocks: list[dict[str, Any]]) -> list[PFMessage]:
    """Convert Anthropic content blocks from a user message.

    User content can mix text, image, and tool_result blocks.  In OpenAI format
    tool_results become separate ``role="tool"`` messages; text+image stay as
    one ``role="user"`` message.
    """
    text_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []
    tool_msgs: list[PFMessage] = []

    for block in blocks:
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "image":
            image_parts.append(_convert_image_block(block))
        elif btype == "tool_result":
            tool_msgs.append(PFMessage(
                role="tool",
                tool_call_id=block.get("tool_use_id", ""),
                content=_extract_tool_result_content(block.get("content", "")),
            ))
        elif btype in ("tool_use", "thinking"):
            # tool_use blocks belong to assistant; thinking has no OpenAI eq.
            pass

    result: list[PFMessage] = []

    # Tool messages MUST come before the user text message: OpenAI requires
    # each assistant tool_calls to be immediately followed by tool messages.
    result.extend(tool_msgs)

    if image_parts:
        content_array: list[dict[str, Any]] = []
        for t in text_parts:
            content_array.append({"type": "text", "text": t})
        content_array.extend(image_parts)
        result.append(PFMessage(role="user", content=content_array))
    elif text_parts:
        result.append(PFMessage(role="user", content="\n".join(text_parts)))

    return result


def _convert_assistant_blocks(blocks: list[dict[str, Any]]) -> PFMessage:
    """Convert Anthropic content blocks from an assistant message.

    Assistant content can mix text, tool_use, and thinking blocks.
    Thinking blocks are preserved as ``reasoning_content`` for the
    OpenAI-format request so that providers requiring multi-turn
    reasoning passthrough (DeepSeek thinking mode, etc.) work correctly.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []

    for block in blocks:
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })
        elif btype in ("thinking", "extended_thinking"):
            thinking_text = block.get("thinking", "") or block.get("text", "")
            if thinking_text:
                reasoning_parts.append(thinking_text)

    msg = PFMessage(role="assistant")
    if text_parts:
        msg.content = "\n".join(text_parts)
    if tool_calls:
        msg.tool_calls = tool_calls
    if reasoning_parts:
        msg.reasoning_content = "\n".join(reasoning_parts)
    return msg


def _convert_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """Anthropic image -> OpenAI image_url block."""
    source = block.get("source", {})
    media_type = source.get("media_type", "image/png")
    data = source.get("data", "")
    url = f"data:{media_type};base64,{data}"
    return {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}


def _extract_tool_result_content(content: Any) -> str:
    """tool_result content: string or list of text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Anthropic tools -> OpenAI tools array.

    Anthropic: {"name": "...", "description": "...", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    if not tools:
        return None
    result: list[dict[str, Any]] = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {}),
            },
        })
    return result


# ═════════════════════════════════════════════════════════════════════
# Reverse conversion: OpenAI Chat Completions → Anthropic Messages
# (for forwarding to Anthropic-native providers)
# ═════════════════════════════════════════════════════════════════════

def openai_to_anthropic_request(openai_req: ChatCompletionRequest) -> dict[str, Any]:
    """Convert an OpenAI ChatCompletionRequest back to Anthropic Messages body.

    This is the reverse of ``anthropic_to_chat_request`` — used when PolicyFlow
    routes to a provider with ``protocol: anthropic`` (e.g. api.anthropic.com).
    """
    messages = list(openai_req.messages)
    system = None
    anthropic_messages: list[dict[str, Any]] = []

    # Pull out the system message (OpenAI role="system" → Anthropic top-level "system")
    if messages and messages[0].role == "system":
        system = messages[0].content
        messages = messages[1:]

    for msg in messages:
        entry: dict[str, Any] = {"role": msg.role}
        if msg.tool_calls:
            # Assistant with tool_calls → Anthropic content blocks
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    inp = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
                    "name": fn.get("name", ""),
                    "input": inp,
                })
            entry["content"] = blocks
        elif msg.role == "tool":
            # OpenAI role="tool" → Anthropic tool_result content block in user message
            prev = anthropic_messages[-1] if anthropic_messages else None
            if prev and prev["role"] == "user" and isinstance(prev.get("content"), list):
                prev["content"].append({
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                })
                continue  # merged into previous user message
            else:
                entry["role"] = "user"
                entry["content"] = [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                }]
        else:
            entry["content"] = msg.content or ""
        anthropic_messages.append(entry)

    # Tools: OpenAI format → Anthropic format
    anthropic_tools = None
    oai_tools = (openai_req.extra or {}).get("tools")
    if oai_tools:
        anthropic_tools = []
        for t in oai_tools:
            fn = t.get("function", {})
            anthropic_tools.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {}),
            })

    body: dict[str, Any] = {
        "model": openai_req.model,
        "messages": anthropic_messages,
        "max_tokens": openai_req.max_tokens or 1024,
        "stream": openai_req.stream,
    }
    if system:
        body["system"] = system
    if openai_req.temperature is not None:
        body["temperature"] = openai_req.temperature
    if openai_req.top_p is not None:
        body["top_p"] = openai_req.top_p
    if openai_req.stop:
        body["stop_sequences"] = openai_req.stop if isinstance(openai_req.stop, list) else [openai_req.stop]
    if anthropic_tools:
        body["tools"] = anthropic_tools

    return body


# ═════════════════════════════════════════════════════════════════════
# Response conversion: OpenAI Chat Completions → Anthropic Messages
# ═════════════════════════════════════════════════════════════════════

def openai_to_anthropic_response(
    openai_data: dict[str, Any],
    routed_model: str,
) -> dict[str, Any]:
    """Convert an OpenAI ChatCompletionResponse (dict) to Anthropic Messages format."""
    choice = (openai_data.get("choices") or [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")
    usage = openai_data.get("usage", {})

    # Convert content + tool_calls to Anthropic content blocks
    content_blocks: list[dict[str, Any]] = []

    # Reasoning / thinking content — preserve for multi-turn passthrough
    # (DeepSeek thinking mode, Gemini thought_signature, etc.)
    reasoning = message.get("reasoning_content") or ""
    if reasoning:
        content_blocks.append({
            "type": "thinking",
            "thinking": reasoning,
            "signature": "",
        })

    # Text content
    text = message.get("content") or ""
    if text:
        content_blocks.append({"type": "text", "text": text})

    # Tool calls -> tool_use blocks
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            inp = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            inp = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
            "name": fn.get("name", ""),
            "input": inp,
        })

    return {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": routed_model,
        "content": content_blocks,
        "stop_reason": _map_finish_reason(finish_reason),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _map_finish_reason(reason: str) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }.get(reason, "end_turn")


# ═════════════════════════════════════════════════════════════════════
# Streaming conversion: OpenAI SSE → Anthropic SSE
# ═════════════════════════════════════════════════════════════════════

class AnthropicStreamConverter:
    """Stateful converter: OpenAI SSE chunks → Anthropic SSE event stream.

    Usage::

        converter = AnthropicStreamConverter(routed_model)
        async for openai_chunk in proxy.chat_completion_stream(request):
            for event in converter.feed(openai_chunk):
                yield event  # bytes ready to send over HTTP SSE
        for event in converter.flush():
            yield event
    """

    def __init__(self, routed_model: str) -> None:
        self.routed_model = routed_model
        self.msg_id = f"msg_{uuid.uuid4().hex[:12]}"

        # State
        self._content_index = -1
        self._has_started = False
        self._finish_reason = "end_turn"
        self._input_tokens = 0
        self._output_tokens = 0
        self._stop_emitted = False
        self._buffer = ""  # Accumulates partial SSE lines across network chunks
        # OpenAI streams tool_calls as fragments (name in the first chunk, the
        # JSON arguments split across later chunks).  Accumulate them by their
        # OpenAI index and emit complete Anthropic tool_use blocks in flush().
        self._tool_calls: dict[int, dict[str, str]] = {}

    def feed(self, chunk_str: str) -> list[bytes]:
        """Feed raw bytes from the upstream stream, return Anthropic SSE events.

        The upstream yields arbitrary network chunks that don't align with SSE
        line boundaries — one chunk may hold several lines, or half a line.  We
        buffer until we have complete lines (split on ``\\n``) and process those,
        keeping any trailing partial line for the next call.
        """
        events: list[bytes] = []
        self._buffer += chunk_str

        # Process all complete lines; keep the trailing partial in the buffer.
        while "\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split("\n", 1)
            events.extend(self._feed_line(raw_line))
        return events

    def _feed_line(self, chunk_str: str) -> list[bytes]:
        """Process one complete SSE line, return Anthropic SSE events to emit."""
        events: list[bytes] = []

        # OpenAI SSE: "data: {...}\n\n"
        line = chunk_str.strip()
        if not line or line == "[DONE]":
            return events
        if line.startswith("data: "):
            line = line[6:]

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return events

        # Extract from the chunk (OpenAI streaming format)
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta", {})
        finish = choice.get("finish_reason")
        usage = data.get("usage")

        # Track usage from the last chunk
        if usage:
            self._input_tokens = usage.get("prompt_tokens", 0)
            self._output_tokens = usage.get("completion_tokens", 0)

        # Start the message on first chunk
        if not self._has_started:
            self._has_started = True
            start_event = _sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": self.msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.routed_model,
                    "content": [],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })
            events.append(start_event)

        # Handle text content
        text = delta.get("content")
        tool_calls = delta.get("tool_calls")

        if text:
            # Start a text content block if this is the first text chunk
            if self._content_index < 0:
                self._content_index += 1
                block_start = _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                })
                events.append(block_start)

            delta_event = _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            })
            events.append(delta_event)

        if tool_calls:
            # Accumulate fragments by index; emit complete blocks in flush().
            for tc in tool_calls:
                idx = tc.get("index", 0)
                slot = self._tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

        if finish:
            self._finish_reason = _map_finish_reason(finish)

        return events

    def flush(self) -> list[bytes]:
        """Emit remaining events after all chunks consumed."""
        if self._stop_emitted:
            return []
        events: list[bytes] = []

        # Close the content block if one was opened
        if self._content_index >= 0:
            events.append(_sse("content_block_stop", {
                "type": "content_block_stop",
                "index": 0,
            }))

        # Emit accumulated tool_use blocks (indexed after any text block)
        next_index = self._content_index + 1
        for _, tc in sorted(self._tool_calls.items()):
            try:
                inp = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                inp = {}
            events.append(_sse("content_block_start", {
                "type": "content_block_start",
                "index": next_index,
                "content_block": {
                    "type": "tool_use",
                    "id": tc["id"] or f"toolu_{uuid.uuid4().hex[:12]}",
                    "name": tc["name"],
                    "input": {},
                },
            }))
            events.append(_sse("content_block_delta", {
                "type": "content_block_delta",
                "index": next_index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(inp, ensure_ascii=False)},
            }))
            events.append(_sse("content_block_stop", {
                "type": "content_block_stop",
                "index": next_index,
            }))
            next_index += 1

        if self._tool_calls:
            self._finish_reason = "tool_use"

        # Message delta with stop reason + usage
        events.append(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self._finish_reason, "stop_sequence": None},
            "usage": {"input_tokens": self._input_tokens, "output_tokens": self._output_tokens},
        }))

        # Stop
        events.append(_sse("message_stop", {
            "type": "message_stop",
        }))

        self._stop_emitted = True
        return events


def _sse(event_type: str, data: dict[str, Any]) -> bytes:
    """Encode one Anthropic SSE event as bytes."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")
