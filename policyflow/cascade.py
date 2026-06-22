"""Cascade validator — rule checks + capability-graded escalation on failure.

Inspired by NadirClaw's cascade design: "分类器不需要完美，先让便宜模型试试，不行再换贵的。"
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .models import ChatCompletionRequest, ChatCompletionResponse

logger = logging.getLogger(__name__)


@dataclass
class CascadeConfig:
    """Cascade validation configuration."""

    enabled: bool = True
    max_retries: int = 2
    escalation_chain: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    """Result of response validation."""

    passed: bool
    reason: str = ""


class CascadeValidator:
    """Rule-based response validation. Escalates to a stronger model on failure."""

    # Patterns that indicate the model refused
    REFUSAL_PATTERNS = [
        r"\bI cannot\b",
        r"\bI can't\b",
        r"\bI am unable to\b",
        r"\bI'm not able to\b",
        r"\bI won't be able to\b",
        r"\bI apologize.{0,30}but I (?:cannot|can't|am unable)\b",
        r"\bAs an AI\b.{0,30}\bI (?:cannot|can't|don't|am not)\b",
        r"\bSorry.{0,20}\bI (?:can'?t|cannot|am not able)\b",
        r"\b无法\b",
        r"\b不能\b",
    ]

    # Minimum response length (characters)
    MIN_RESPONSE_LENGTH = 10

    def __init__(self, config: CascadeConfig) -> None:
        self.config = config

    def validate(
        self, response: ChatCompletionResponse, request: ChatCompletionRequest
    ) -> ValidationResult:
        """Run rule-based validation."""
        if not response.choices:
            return ValidationResult(False, "no_choices")

        content = self._extract_content(response)
        if not content:
            return ValidationResult(False, "empty_content")

        # Check 1: Refusal
        if self._check_refusal(content):
            return ValidationResult(False, "refusal_detected")

        # Check 2: Truncation (response ends mid-sentence)
        if self._check_truncation(content):
            return ValidationResult(False, "truncated")

        # Check 3: Empty/short response
        if len(content.strip()) < self.MIN_RESPONSE_LENGTH:
            return ValidationResult(False, f"too_short ({len(content)} chars)")

        # Check 4: JSON format requested but not returned
        if self._wants_json(request) and not self._is_valid_json(content):
            return ValidationResult(False, "json_expected_but_invalid")

        return ValidationResult(True, "ok")

    def get_next_model(
        self,
        current_model: str,
        specialty: str = "",
        available_models: list[str] | None = None,
    ) -> str | None:
        """Get the next escalation target.

        Preferred path (capability escalation): when a task type and the set
        of available models are known, escalate to the next model UP by pure
        capability — one step stronger than current, price ignored (the cheap
        model already failed, so quality is what matters now).

        Fallback path: the static escalation_chain from config (used when
        capability info is unavailable or yields nothing).
        """
        if specialty and available_models:
            from .model_profiles import next_stronger_model
            nxt = next_stronger_model(specialty, current_model, available_models)
            if nxt:
                return nxt
        # Static chain fallback
        try:
            idx = self.config.escalation_chain.index(current_model)
            if idx + 1 < len(self.config.escalation_chain):
                return self.config.escalation_chain[idx + 1]
        except ValueError:
            if self.config.escalation_chain:
                return self.config.escalation_chain[0]
        return None

    @staticmethod
    def _extract_content(response: ChatCompletionResponse) -> str:
        """Extract text content from the first choice. Safe on empty choices."""
        if not response.choices:
            return ""
        choice = response.choices[0]
        if choice.message and choice.message.content:
            content = choice.message.content
            return content if isinstance(content, str) else str(content)
        if choice.delta and choice.delta.content:
            return choice.delta.content
        return ""

    @classmethod
    def _check_refusal(cls, content: str) -> bool:
        """Check if the response contains refusal language."""
        return any(re.search(p, content, re.IGNORECASE) for p in cls.REFUSAL_PATTERNS)

    @staticmethod
    def _check_truncation(content: str) -> bool:
        """Check if the response appears truncated (ends abruptly)."""
        content = content.rstrip()
        if not content:
            return False
        # Ends with common truncation markers
        truncation_endings = [",", ";", ":", " of", " the", " a", " an", " to", " and", " or"]
        for ending in truncation_endings:
            if content.endswith(ending):
                return True
        # Ends with incomplete code block
        if "```" in content and content.count("```") % 2 != 0:
            return True
        return False

    @staticmethod
    def _wants_json(request: ChatCompletionRequest) -> bool:
        """Check if the user likely expects a JSON response.

        Only looks at user messages — assistant tool calls and system-reminder
        injections routinely contain JSON-related text that would trigger a
        false positive for every request in an agent session.
        """
        prompt_lower = ""
        for msg in request.messages:
            if msg.role != "user":
                continue
            content = msg.content
            if isinstance(content, str):
                prompt_lower += content.lower() + " "
        json_keywords = ["json", "json format", "return json", "output json", "valid json"]
        return any(kw in prompt_lower for kw in json_keywords)

    @staticmethod
    def _is_valid_json(content: str) -> bool:
        """Check if content is (or contains) valid JSON."""
        content = content.strip()
        # Try direct parse
        try:
            json.loads(content)
            return True
        except (json.JSONDecodeError, ValueError):
            pass
        # Try extracting JSON from markdown code blocks
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if match:
            try:
                json.loads(match.group(1).strip())
                return True
            except (json.JSONDecodeError, ValueError):
                pass
        return False
