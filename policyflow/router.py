"""Router — orchestrates policy matching + embedding classification → rewrites model."""

from __future__ import annotations

import hashlib
import logging
import time

from .classifier import EmbeddingClassifier
from .config import Config
from .model_profiles import select_best_model
from .models import ChatCompletionRequest
from .policy import Policy, PolicyEngine

logger = logging.getLogger(__name__)


def _session_key(request: ChatCompletionRequest) -> str:
    """Derive a stable session key from the system prompt + first user message.

    Same conversation → same key across turns, because system and the opening
    user message stay constant while later turns are appended.  No client header
    required.  Returns "" when there's no usable content (don't track).
    """
    parts: list[str] = []
    for msg in request.messages:
        if msg.role in ("system", "developer"):
            content = msg.content if isinstance(msg.content, str) else ""
            parts.append(f"sys:{content[:200]}")
            break
    for msg in request.messages:
        if msg.role == "user":
            content = msg.content if isinstance(msg.content, str) else ""
            parts.append(f"usr:{content[:200]}")
            break
    if not parts:
        return ""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class SessionMemory:
    """Remembers the model chosen for each conversation, for continuation turns.

    A short follow-up like "继续" matches no policy and has no semantic content,
    so it falls through to Phase 4.  When that happens and we've seen this
    conversation before, we reuse the previous turn's model instead of dropping
    to the fallback — the follow-up continues whatever task was underway.
    """

    def __init__(self, ttl: int = 1800) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[str, float]] = {}  # key → (model, expiry)

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry and time.time() <= entry[1]:
            return entry[0]
        if entry:
            del self._store[key]
        return None

    def set(self, key: str, model: str) -> None:
        self._store[key] = (model, time.time() + self.ttl)


def _extract_prompt(request: ChatCompletionRequest) -> str:
    """Extract the text of the *latest* user message for routing.

    Only the current turn is routed — not the whole history.  This is what lets
    a continuation turn ("继续") fall through to Phase 4 (where the session's
    previous model is reused) instead of being dragged into a policy by earlier
    turns' keywords.  It also keeps a long coding session from pinning every
    later turn to the topic of its opening messages.
    """
    for msg in reversed(request.messages):
        if msg.role != "user":
            continue
        content = msg.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "\n".join(parts).strip()
    return ""


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
        self.engine = PolicyEngine(config.policies_data, config.routing_mode)
        self.classifier = EmbeddingClassifier(
            base_url=config.embedding_base_url,
            api_key=config.embedding_api_key,
            model=config.embedding_model,
            threshold=config.embedding_threshold,
            timeout=config.embedding_timeout,
        )
        self.verify_threshold = config.embedding_verify_threshold
        self.cost_tier_thresholds = config.cost_tier_thresholds
        self.sessions = SessionMemory()
        self.fallback_model = config.upstream_fallback_model
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
        2. Keyword exact match + Embedding verification (catches false hits like
           "苹果手机" matching a fruit policy)
        3. Embedding similarity match (main classification path)
        4. Default policy — fallback

        When a policy uses `specialty` (capability routing), the best model
        is selected automatically by capability score within the policy's
        max_cost_tier budget (the tier bounds the pool; the score picks the
        most capable model in it).
        """
        available = self.config.available_models
        prompt = _extract_prompt(request)
        token_estimate = _estimate_tokens(prompt)
        has_img = _has_image(request.messages)

        def decide(policy: Policy | None, method: str, score: float) -> RouteDecision:
            """Construct a RouteDecision with all router-level context wired in."""
            return RouteDecision(
                policy, method, score,
                available_models=available,
                use_capability=self.engine.uses_capability_routing(policy) if policy else False,
                cost_tier_thresholds=self.cost_tier_thresholds,
                original_model=request.model,
            )

        session_key = _session_key(request)

        def finalize(decision: RouteDecision) -> RouteDecision:
            """Record this turn's model for the session, then return the decision.

            Lets a later continuation turn ("继续") reuse this model in Phase 4.
            """
            if session_key:
                self.sessions.set(session_key, decision.target_model)
            return decision

        # Phase 1: Image detection (explicit rule)
        for policy in self.engine.non_default_policies:
            if policy.has_image and has_img:
                return finalize(decide(policy, "image_match", 1.0))

        # Embed the prompt once — reused by both keyword verification and
        # global embedding match. None if embedding API is unavailable, in
        # which case keyword matches are trusted without verification.
        prompt_emb = None
        if prompt and self.classifier.policy_embeddings:
            try:
                prompt_emb = await self.classifier.embed_prompt(prompt)
            except Exception as exc:
                logger.warning("Embedding API unavailable, keyword matches will not be verified: %s", exc)

        # Phase 2: Keyword exact match + embedding verification
        for policy in self.engine.non_default_policies:
            if policy.has_image:
                continue
            if policy.max_input_tokens and token_estimate > policy.max_input_tokens:
                continue
            if policy.min_input_tokens and token_estimate < policy.min_input_tokens:
                continue
            if not policy.keywords:
                continue
            prompt_lower = prompt.lower()
            if not any(kw.lower() in prompt_lower for kw in policy.keywords):
                continue

            # Keyword hit — verify with embedding similarity if available.
            # If embedding is down, trust the keyword (graceful degradation).
            if prompt_emb is None:
                return finalize(decide(policy, "keyword_match", 1.0))
            similarity = self.classifier.similarity_to(prompt_emb, policy.name)
            if similarity >= self.verify_threshold:
                return finalize(decide(policy, "keyword_verified", similarity))
            logger.info(
                "Keyword hit on policy %r overridden by verification (similarity=%.3f < %.3f)",
                policy.name, similarity, self.verify_threshold,
            )

        # Phase 3: Embedding similarity match
        embed_score = 0.0
        if prompt_emb is not None:
            try:
                policy_name, embed_score = self.classifier.match(prompt_emb)
                if policy_name:
                    for p in self.engine.policies:
                        if p.name == policy_name:
                            return finalize(decide(p, "embedding_match", embed_score))
                else:
                    logger.info(
                        "Embedding: best match below threshold (max=%.3f, threshold=%.3f)",
                        embed_score, self.classifier.threshold,
                    )
            except Exception as exc:
                logger.warning("Embedding classification failed, no policy matched: %s", exc)

        # Phase 4: Nothing matched. A continuation turn ("继续") carries no
        # routable signal — reuse the session's previous model if we have one;
        # otherwise fall back to the single fallback model.
        if session_key:
            prev = self.sessions.get(session_key)
            if prev:
                return decide(None, "session_continuation", embed_score).with_model(prev)

        return decide(None, "fallback", 0.0).with_model(self.fallback_model)


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
        use_capability: bool = False,
        cost_tier_thresholds: dict[str, float] | None = None,
    ) -> None:
        self.policy = policy
        self.method = method
        self.score = score
        if policy and use_capability and available_models:
            from .policy import PolicyEngine
            from .model_profiles import select_best_models
            task = PolicyEngine._infer_specialty(policy)
            top = select_best_models(
                task, available_models, n=3,
                cost_tier=policy.max_cost_tier,
                cost_tier_thresholds=cost_tier_thresholds,
            )
            if top:
                # Weighted random among Top-3 (90/7/3) — keeps the #1
                # as the primary workhorse while giving #2/#3 a small
                # share for fault-tolerance warm-up and quota smoothing.
                import random
                weights = [0.90, 0.07, 0.03][:len(top)]
                self.target_model = random.choices(top, weights=weights)[0]
            else:
                self.target_model = policy.route_to or original_model
            if top:
                self.method = f"capability({task})"
        else:
            self.target_model = policy.route_to if policy else original_model

    def with_model(self, model: str) -> "RouteDecision":
        """Override the resolved target model (Phase 4 continuation/fallback)."""
        self.target_model = model
        return self

    def __repr__(self) -> str:
        return (
            f"RouteDecision(model={self.target_model!r}, "
            f"policy={self.policy.name if self.policy else 'none'}, "
            f"method={self.method!r}, score={self.score:.3f})"
        )
