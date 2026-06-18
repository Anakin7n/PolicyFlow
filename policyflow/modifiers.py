"""Smart modifiers — pre-routing checks that override or adjust the routing decision.

All modifiers are cheap rule-based checks: no API calls, no model inference.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from .models import ChatCompletionRequest

logger = logging.getLogger(__name__)

# ── Model capability tables (hardcoded for common models) ──────────

# Approximate context window sizes (tokens)
MODEL_WINDOWS: dict[str, int] = {
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-opus-4-7": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_385,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.5-pro": 1_000_000,
    "deepseek-v3": 128_000,
    "deepseek-r1": 128_000,
}

# Models suitable for reasoning tasks
REASONING_MODELS = [
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "o1",
    "o1-mini",
    "o3-mini",
    "deepseek-r1",
    "gemini-2.5-pro",
]

# Vision-capable models
VISION_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
]

# ── Token estimation ───────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def estimate_request_tokens(request: ChatCompletionRequest) -> int:
    """Estimate total tokens in a chat request."""
    total = 0
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(str(block.get("text", "")))
    return max(1, total)


# ── Agent detection ─────────────────────────────────────────────────

AGENT_SYSTEM_MARKERS = [
    "you are an agent", "you are a coding agent", "you have access to tools",
    "function calling", "tool call", "tool_choice",
]

AGENT_PROMPT_MARKERS = [
    "use the tool", "call the function", "run this command",
    "execute the following", "invoke the",
]


def detect_agent(request: ChatCompletionRequest) -> bool:
    """Check if this request is from an AI agent (has tools or agent-style prompts)."""
    # Check for tools array in request extras
    if hasattr(request, "tools") and getattr(request, "tools", None):
        return True

    # Check for tool-role messages
    for msg in request.messages:
        if msg.role == "tool":
            return True
        if msg.tool_calls:
            return True
        if msg.tool_call_id:
            return True

    # Check system prompt for agent markers
    for msg in request.messages:
        if msg.role == "system" and isinstance(msg.content, str):
            content_lower = msg.content.lower()
            if any(m in content_lower for m in AGENT_SYSTEM_MARKERS):
                return True

    # Check user/assistant content for tool-use patterns
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            content_lower = content.lower()
            if any(m in content_lower for m in AGENT_PROMPT_MARKERS):
                return True

    return False


# ── Reasoning detection ─────────────────────────────────────────────

REASONING_MARKERS = [
    "step by step", "prove that", "proof", "analyze tradeoffs",
    "think carefully", "reason about", "explain your reasoning",
    "分析", "推理", "证明", "论证", "逐步思考",
    "trade-off", "tradeoff", "architecture decision",
    "system design", "安全审计", "性能优化",
]


def detect_reasoning(prompt: str) -> bool:
    """Check if the prompt asks for complex reasoning (2+ markers)."""
    prompt_lower = prompt.lower()
    hits = sum(1 for m in REASONING_MARKERS if m.lower() in prompt_lower)
    return hits >= 2


# ── Context window filter ───────────────────────────────────────────

def get_model_window(model_id: str) -> int:
    """Get the context window size for a model. Returns 0 if unknown."""
    # Try exact match first, then prefix match
    if model_id in MODEL_WINDOWS:
        return MODEL_WINDOWS[model_id]
    for prefix, window in MODEL_WINDOWS.items():
        if model_id.startswith(prefix):
            return window
    return 0


def find_larger_window_model(
    current_model: str, required_tokens: int
) -> str | None:
    """Find a model with a larger context window that fits the request."""
    current_window = get_model_window(current_model)
    if current_window >= required_tokens:
        return None

    # Find the smallest model that fits
    candidates = [
        (m, w) for m, w in MODEL_WINDOWS.items()
        if w >= required_tokens and m != current_model
    ]
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0] if candidates else None


# ── Session persistence ─────────────────────────────────────────────

class SessionStore:
    """Simple in-memory session store with TTL-based expiry."""

    def __init__(self, ttl: int = 1800) -> None:  # 30 min default
        self.ttl = ttl
        self._store: dict[str, tuple[str, float]] = {}  # session_id → (model, expiry)

    def _cleanup(self) -> None:
        """Remove expired entries."""
        now = time.time()
        expired = [sid for sid, (_, exp) in self._store.items() if now > exp]
        for sid in expired:
            del self._store[sid]

    def get(self, session_id: str) -> str | None:
        """Get the cached model for a session, or None if expired/missing."""
        self._cleanup()
        entry = self._store.get(session_id)
        if entry and time.time() <= entry[1]:
            return entry[0]
        if entry:
            del self._store[session_id]
        return None

    def set(self, session_id: str, model: str) -> None:
        """Cache a model for a session."""
        self._store[session_id] = (model, time.time() + self.ttl)

    def __len__(self) -> int:
        self._cleanup()
        return len(self._store)


# ── Modifier engine ─────────────────────────────────────────────────

class ModifierResult:
    """Result from modifier checks — may override the routing decision."""

    __slots__ = ("override_model", "reason")

    def __init__(self, override_model: str | None = None, reason: str = ""):
        self.override_model = override_model
        self.reason = reason

    @property
    def has_override(self) -> bool:
        return self.override_model is not None


class ModifierEngine:
    """Runs all pre-routing modifiers in priority order."""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or {}
        self.agent_detection = cfg.get("agent_detection", True)
        self.reasoning_detection = cfg.get("reasoning_detection", True)
        self.context_window_filter = cfg.get("context_window_filter", True)
        self.session_persistence = cfg.get("session_persistence", True)
        self.strongest_model = cfg.get("strongest_model", "claude-opus-4-8")
        self.reasoning_model = cfg.get("reasoning_model", "claude-opus-4-8")
        self.sessions = SessionStore(ttl=cfg.get("session_ttl", 1800))

    def run(
        self,
        request: ChatCompletionRequest,
        session_id: str | None,
    ) -> ModifierResult:
        """Run all enabled modifiers. Returns first override found.

        Priority order:
        1. Agent detection → strongest model (skip all routing)
        2. Session persistence → cached model
        3. Reasoning detection → reasoning model
        4. Context window → larger-window model
        """

        # Extract prompt text for checks
        prompt = _extract_prompt_text(request)

        # 1. Agent detection
        if self.agent_detection and detect_agent(request):
            return ModifierResult(self.strongest_model, "agent_detected")

        # 2. Session persistence
        if self.session_persistence and session_id:
            cached = self.sessions.get(session_id)
            if cached:
                return ModifierResult(cached, "session_persist")

        # 3. Reasoning detection
        if self.reasoning_detection and detect_reasoning(prompt):
            return ModifierResult(self.reasoning_model, "reasoning_detected")

        # 4. Context window filter
        if self.context_window_filter:
            estimated = estimate_request_tokens(request)
            current_model = request.model
            current_window = get_model_window(current_model)
            if current_window and estimated > current_window:
                larger = find_larger_window_model(current_model, estimated)
                if larger:
                    return ModifierResult(larger, "context_window")

        return ModifierResult()

    def persist_session(self, session_id: str | None, model: str) -> None:
        """Store the routing decision for session persistence."""
        if self.session_persistence and session_id:
            self.sessions.set(session_id, model)


def _extract_prompt_text(request: ChatCompletionRequest) -> str:
    """Extract text content from all messages for modifier checks."""
    parts: list[str] = []
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts)
