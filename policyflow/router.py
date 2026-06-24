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
    """Remembers the model chosen for each conversation across all later turns.

    Routing decisions are made on the *first* turn only.  Every subsequent turn
    on the same conversation reuses the recorded model — this preserves the
    upstream provider's prompt cache (Anthropic / DeepSeek cache hits cost
    1/10 – 1/120 of misses; switching models mid-conversation evicts the
    50k+ token prefix that agent clients like Claude Code accumulate, often
    making the per-token bill several times higher than not routing at all).

    The cached model is only overwritten when the cascade validator
    successfully escalates to a stronger model (see ``promote``) — that's the
    sole intended source of mid-conversation model changes.
    """

    def __init__(self, ttl: int = 1800, max_size: int = 10_000) -> None:
        self.ttl = ttl
        self.max_size = max_size
        self._store: dict[str, tuple[str, str, float]] = {}  # key → (model, policy_name, expiry)

    def get(self, key: str) -> tuple[str | None, str | None]:
        entry = self._store.get(key)
        if entry and time.time() <= entry[2]:
            return entry[0], entry[1]
        if entry:
            del self._store[key]
        return None, None

    def set(self, key: str, model: str, policy_name: str = "") -> None:
        if len(self._store) >= self.max_size:
            oldest = min(self._store, key=lambda k: self._store[k][2])
            del self._store[oldest]
        self._store[key] = (model, policy_name, time.time() + self.ttl)

    def promote(self, key: str, model: str) -> None:
        """Replace the cached model after a successful cascade escalation.

        Keeps the original policy_name (the task type didn't change, only the
        model serving it).  No-op if the session was never recorded — the next
        call site will record it normally.
        """
        entry = self._store.get(key)
        if entry is None:
            return
        _, policy_name, expiry = entry
        self._store[key] = (model, policy_name, expiry)


def _extract_prompt(request: ChatCompletionRequest) -> str:
    """Return the text of the last real human message.

    Skips XML-injected ``role=user`` blocks (system-reminders, tool results)
    that agent clients like Claude Code emit between turns — those blocks
    aren't a fresh user prompt and shouldn't drive classification.
    """
    last_user = None
    for msg in request.messages:
        if msg.role == "user":
            last_user = msg

    for msg in reversed(request.messages):
        if msg.role != "user":
            continue
        content = msg.content
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "\n".join(parts).strip()

        if not text:
            continue
        if not text.startswith("<"):
            return text

    # All user messages appear injected — fall back to the last one we saw.
    if last_user is not None:
        c = last_user.content
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text").strip()
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

        First-turn classification, then sticky for the rest of the conversation
        ============================================================
        A full Phase 1-4 classification runs only on the *first* turn of a
        conversation (no SessionMemory entry yet, or it has expired).  Every
        subsequent turn on the same session — identified by the system + first
        user message hash — short-circuits to the recorded model, skipping
        embedding lookup entirely.

        Why: agent clients like Claude Code accumulate 50k+ tokens of stable
        prefix (system prompt + tool definitions + history) per turn.  Upstream
        prompt cache (Anthropic / DeepSeek) discounts the cached prefix 10-120x
        on hits, but the cache is keyed per (provider, model) — switching
        models mid-conversation evicts the entire prefix.  Re-classifying every
        turn was making the bill *worse*, not better.  The cascade validator
        (see ``SessionMemory.promote``) is the only mechanism allowed to change
        the model mid-conversation.

        First-turn decision priority:
        1. Image detection → visual model (explicit rule)
        2. Keyword exact match + Embedding verification (catches false hits)
        3. Embedding similarity match (main classification path)
        4. Default fallback model
        """
        available = self.config.available_models
        session_key = _session_key(request)

        def decide(policy: Policy | None, method: str, score: float) -> RouteDecision:
            """Construct a RouteDecision with all router-level context wired in."""
            d = RouteDecision(
                policy, method, score,
                available_models=available,
                use_capability=self.engine.uses_capability_routing(policy) if policy else False,
                cost_tier_thresholds=self.cost_tier_thresholds,
                original_model=request.model,
            )
            d.session_key = session_key
            return d

        # Sticky fast path: every turn after the first reuses the recorded
        # model.  No classification, no embedding API call, no policy match —
        # the upstream prompt cache keeps paying off.
        if session_key:
            prev_model, prev_policy = self.sessions.get(session_key)
            if prev_model:
                d = decide(None, "session_sticky", 0.0).with_model(prev_model).with_inherited_policy(prev_policy)
                d.session_status = "sticky"
                return d

        # First turn — run the full classifier pipeline below.
        prompt = _extract_prompt(request)
        token_estimate = _estimate_tokens(prompt)
        has_img = _has_image(request.messages)

        def finalize(decision: RouteDecision) -> RouteDecision:
            """Record this turn's model in SessionMemory so later turns stick to it."""
            if session_key:
                pn = decision.policy.name if decision.policy else ""
                self.sessions.set(session_key, decision.target_model, pn)
            decision.session_status = "first"
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

        # Phase 4: Nothing matched — first turn falls through to the fallback
        # model.  Record it so subsequent turns also stick to this choice.
        return finalize(decide(None, "fallback", 0.0).with_model(self.fallback_model))


class RouteDecision:
    """Result of a routing decision."""

    __slots__ = ("policy", "method", "score", "target_model", "inherited_policy_name", "session_key", "session_status")

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
        self.inherited_policy_name = ""
        self.session_key = ""
        self.session_status = ""  # "first" | "sticky" | "escalated" — filled in by Router
        if policy and use_capability and available_models:
            from .model_profiles import select_best_models
            task = policy.name
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

    def with_inherited_policy(self, policy_name: str) -> "RouteDecision":
        """Carry the first turn's policy through sticky follow-up turns."""
        self.inherited_policy_name = policy_name
        return self

    def __repr__(self) -> str:
        return (
            f"RouteDecision(model={self.target_model!r}, "
            f"policy={self.policy.name if self.policy else 'none'}, "
            f"method={self.method!r}, score={self.score:.3f})"
        )
