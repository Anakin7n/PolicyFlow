"""OpenAI-compatible request/response models."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────

class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False
    temperature: float | None = 0.7
    top_p: float | None = 1.0
    n: int = 1
    max_tokens: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = 0.0
    frequency_penalty: float | None = 0.0
    user: str | None = None
    # Allow extra fields (some providers send additional params)
    extra: dict[str, Any] = Field(default_factory=dict, exclude=True)

    class Config:
        extra = "allow"


# ── Response ─────────────────────────────────────────────────────────

class ChoiceDelta(BaseModel):
    role: str | None = None
    content: str | None = None


class Choice(BaseModel):
    index: int
    message: Message | None = None
    delta: ChoiceDelta | None = None
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage | None = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "routekit"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]
