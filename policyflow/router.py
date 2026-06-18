"""Router — orchestrates policy matching + embedding classification → rewrites model."""

from __future__ import annotations

import logging

from .classifier import EmbeddingClassifier
from .config import Config
from .model_profiles import select_best_model
from .models import ChatCompletionRequest
from .policy import Policy, PolicyEngine

logger = logging.getLogger(__name__)


def _extract_prompt(request: ChatCompletionRequest) -> str:
    """Extract the user-facing prompt text from a chat request for embedding."""
    parts: list[str] = []
    for msg in request.messages:
        content = msg.content
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            # Multi-modal content: extract text parts
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (conservative)."""
    return max(1, len(text) // 4)


def _has_image(messages) -> bool:
    """Check if any message contains an image_url."""
    for msg in messages:
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    return True
    return False


class Router:
    """Policy-aware router that decides which model to use for each request."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.engine = PolicyEngine(config.policies_data)
        self.classifier = EmbeddingClassifier(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model,
            threshold=config.embedding_threshold,
            timeout=config.embedding_timeout,
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Pre-compute policy keyword embeddings at startup.

        If the embedding API is unreachable, the router still starts —
        it will use keyword matching + default policy only.
        """
        if self._initialized:
            return
        try:
            await self.classifier.init_policies(self.engine.non_default_policies)
        except Exception as exc:
            logger.warning(
                "Cannot reach embedding API (%s). "
                "Embedding-based classification disabled; keyword match + default only.",
                exc,
            )
        self._initialized = True
        logger.info(
            "Router initialized: %d policies, %d with keyword embeddings",
            len(self.engine.policies),
            len(self.classifier.policy_embeddings),
        )

    async def close(self) -> None:
        await self.classifier.close()

    async def route(self, request: ChatCompletionRequest) -> RouteDecision:
        """Decide which model to route this request to.

        Decision priority:
        1. Image detection → visual model (explicit rule)
        2. Keyword exact match (cheap, no API call)
        3. Embedding similarity match (main classification path)
        4. Default policy — fallback

        When a policy uses `specialty` (capability routing), the best model
        is selected automatically based on capability scores + cost.
        """
        available = list(self.config._model_provider.keys())
        prompt = _extract_prompt(request)
        token_estimate = _estimate_tokens(prompt)
        has_img = _has_image(request.messages)

        # Phase 1: Image detection (explicit rule)
        for policy in self.engine.non_default_policies:
            if policy.has_image and has_img:
                return RouteDecision(policy, "image_match", 1.0, available_models=available)

        # Phase 2: Keyword exact match (case-insensitive substring)
        for policy in self.engine.non_default_policies:
            if policy.has_image:
                continue
            if policy.max_input_tokens and token_estimate > policy.max_input_tokens:
                continue
            if policy.min_input_tokens and token_estimate < policy.min_input_tokens:
                continue
            if policy.keywords:
                prompt_lower = prompt.lower()
                if any(kw.lower() in prompt_lower for kw in policy.keywords):
                    return RouteDecision(policy, "keyword_match", 1.0, available_models=available)

        # Phase 3: Embedding similarity match
        if prompt and self.classifier.policy_embeddings:
            try:
                prompt_emb = await self.classifier.embed_prompt(prompt)
                policy_name, score = self.classifier.match(prompt_emb)
                if policy_name:
                    for p in self.engine.policies:
                        if p.name == policy_name:
                            return RouteDecision(p, "embedding_match", score, available_models=available)
            except Exception as exc:
                logger.warning("Embedding classification failed, falling back to default: %s", exc)

        # Phase 4: Default
        default = self.engine.default
        if default:
            return RouteDecision(default, "default", 0.0, available_models=available)

        # Ultimate fallback: keep original model
        return RouteDecision(None, "passthrough", 0.0, original_model=request.model)


class RouteDecision:
    """Result of a routing decision."""

    __slots__ = ("policy", "method", "score", "target_model")

    def __init__(
        self,
        policy: Policy | None,
        method: str,
        score: float,
        original_model: str = "",
        available_models: list[str] | None = None,
    ) -> None:
        self.policy = policy
        self.method = method
        self.score = score
        if policy and policy.uses_capability_routing and available_models:
            best = select_best_model(
                policy.specialty, available_models,
                cost_tier=policy.max_cost_tier,
            )
            self.target_model = best or policy.route_to or original_model
            if best:
                self.method = f"capability({policy.specialty})"
        else:
            self.target_model = policy.route_to if policy else original_model

    def __repr__(self) -> str:
        return (
            f"RouteDecision(model={self.target_model!r}, "
            f"policy={self.policy.name if self.policy else 'none'}, "
            f"method={self.method!r}, score={self.score:.3f})"
        )
