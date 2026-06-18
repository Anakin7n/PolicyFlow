"""PolicyFlow — FastAPI application entry point.

Week 6: multi-provider routing + LLM-as-Judge cascade + CLI + AI optimizer.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import StreamingResponse

from . import db
from .db import hash_prompt
from .cascade import CascadeConfig, CascadeValidator
from .config import Config
from .cost import calc_compared_cost, calc_cost
from .models import ChatCompletionRequest, ChatCompletionResponse, Message, ModelsResponse
from .modifiers import ModifierEngine
from .proxy import ProxyError, UpstreamProxy
from .router import Router

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, config, and all components. Shutdown: clean up."""
    db.init_db()
    config = Config()
    proxy = UpstreamProxy(config)
    router = Router(config)
    cascade_config = CascadeConfig(**config.cascade_data)
    cascade = CascadeValidator(cascade_config)

    # Build LLM-as-Judge function if configured
    judge_fn = None
    if cascade_config.verifier in ("llm_judge", "rule_then_llm") and cascade_config.judge_model:
        async def _judge(prompt: str, response_text: str) -> tuple[bool, str]:
            judge_req = ChatCompletionRequest(
                model=cascade_config.judge_model,
                messages=[Message(role="user", content=cascade_config.judge_prompt.format(
                    prompt=prompt[:4000], response=response_text[:4000]
                ))],
            )
            try:
                jr = await proxy.chat_completion(judge_req)
                text = (jr.choices[0].message.content or "").strip()
                passed = text.upper().startswith("PASS")
                reason = "" if passed else text[5:].strip()[:200] if text.startswith("FAIL") else text[:200]
                return passed, reason
            except Exception:
                return True, "judge_error"
        judge_fn = _judge
        cascade = CascadeValidator(cascade_config, judge_fn=judge_fn)
    modifiers = ModifierEngine(config.modifiers_data)

    await router.initialize()

    app.state.config = config
    app.state.proxy = proxy
    app.state.router = router
    app.state.cascade = cascade
    app.state.modifiers = modifiers

    logger.info("PolicyFlow v0.5.0 started")

    try:
        yield
    finally:
        await router.close()
        await proxy.close()


app = FastAPI(
    title="PolicyFlow",
    description='策略路由中间件 — 多供应商路由 + 级联验证 + CLI 成本分析',
    version="0.5.0",
    lifespan=lifespan,
)



# ── OpenAI-compatible endpoints ──────────────────────────────────────

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    fastapi_request: FastAPIRequest,
):
    """Chat completions with full pipeline: modifiers → router → cascade → log."""
    router: Router = app.state.router
    proxy: UpstreamProxy = app.state.proxy
    cascade: CascadeValidator = app.state.cascade
    modifiers: ModifierEngine = app.state.modifiers
    config: Config = app.state.config

    t_start = time.time()
    original_model = request.model

    # Hash the last user message for logging
    last_msg = request.messages[-1].content if request.messages else ""
    prompt_text = last_msg if isinstance(last_msg, str) else str(last_msg)
    prompt_hash_val = hash_prompt(prompt_text) if prompt_text else ""
    prompt_preview_val = prompt_text[:500] if config.log_prompt_preview else ""
    judge_reason_val = ""
    session_id = fastapi_request.headers.get("X-Session-ID")
    user = fastapi_request.headers.get("X-User", "default")
    cascade_attempts = 0
    success = True

    # ── Step 1: Modifiers (pre-routing overrides) ──────────────────
    modifier_result = modifiers.run(request, session_id)

    if modifier_result.has_override:
        request.model = modifier_result.override_model
        route_method = modifier_result.reason
        route_score = 1.0
        route_policy_name = modifier_result.reason
    else:
        # ── Step 2: Policy routing ─────────────────────────────────
        decision = await router.route(request)
        request.model = decision.target_model
        route_method = decision.method
        route_score = decision.score
        route_policy_name = decision.policy.name if decision.policy else "none"

    logger.info(
        "Route: %s → %s  [%s, %.3f]",
        original_model, request.model, route_method, route_score,
    )

    # ── Step 3: Persist session ────────────────────────────────────
    modifiers.persist_session(session_id, request.model)

    # ── Step 4: Forward + cascade ──────────────────────────────────
    try:
        if request.stream:
            response = None  # Streaming: can't extract usage for logging
            provider_name = proxy.config.get_model_provider(request.model)
            return _stream_response(proxy, request, provider_name, route_policy_name, route_method, route_score)
        else:
            response, cascade_attempts, judge_reason_val = await _forward_with_cascade(
                proxy, cascade, request
            )
    except HTTPException:
        success = False
        raise
    finally:
        # ── Step 5: Log to database ────────────────────────────────
        duration_ms = int((time.time() - t_start) * 1000)
        _log_to_db(
            user=user,
            original_model=original_model,
            routed_model=request.model,
            policy_name=route_policy_name,
            method=route_method,
            score=route_score,
            response=response,
            cascade_attempts=cascade_attempts,
            duration_ms=duration_ms,
            success=success,
            prompt_hash=prompt_hash_val,
            prompt_preview=prompt_preview_val,
            judge_reason=judge_reason_val,
        )

    return response


def _log_to_db(
    user: str,
    original_model: str,
    routed_model: str,
    policy_name: str,
    method: str,
    score: float,
    response: ChatCompletionResponse | None,
    cascade_attempts: int,
    duration_ms: int,
    success: bool,
    prompt_hash: str = "",
    prompt_preview: str = "",
    judge_reason: str = "",
) -> None:
    """Record the request and its costs to SQLite."""
    prompt_tokens = 0
    completion_tokens = 0

    if response and response.usage:
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens

    estimated_cost = calc_cost(routed_model, prompt_tokens, completion_tokens)
    compared_cost = calc_compared_cost(prompt_tokens, completion_tokens)

    try:
        db.log_request(
            user=user,
            original_model=original_model,
            routed_model=routed_model,
            policy_name=policy_name,
            method=method,
            similarity_score=score,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimated_cost,
            compared_cost=compared_cost,
            cascade_attempts=cascade_attempts,
            duration_ms=duration_ms,
            success=success,
            prompt_hash=prompt_hash,
            prompt_preview=prompt_preview,
            judge_reason=judge_reason,
        )
    except Exception as exc:
        logger.warning("Failed to log request: %s", exc)


async def _forward_with_cascade(
    proxy: UpstreamProxy,
    cascade: CascadeValidator,
    request: ChatCompletionRequest,
) -> tuple[ChatCompletionResponse, int, str]:
    """Forward request with cascade escalation on validation failure.

    Returns (response, cascade_attempts, judge_reason).
    """
    max_attempts = cascade.config.max_retries + 1
    cascade_attempts = 0
    judge_reason = ""
    verifier = cascade.config.verifier

    for attempt in range(max_attempts):
        current_model = request.model

        try:
            provider_name = proxy.config.get_model_provider(request.model)
            response = await proxy.chat_completion(request, provider_name=provider_name)
        except ProxyError as e:
            logger.warning("Upstream error (attempt %d): %s", attempt + 1, e)
            next_model = cascade.get_next_model(current_model)
            if next_model and attempt < max_attempts - 1:
                request.model = next_model
                cascade_attempts += 1
                logger.info("Fallback: %s → %s", current_model, next_model)
                continue
            raise HTTPException(status_code=502, detail=str(e))

        # ── Rule-based validation (skip if verifier is llm_judge-only) ──
        if verifier != "llm_judge":
            validation = cascade.validate(response, request)
            if not validation.passed:
                logger.info(
                    "Cascade fail [%s]: %s → escalating (attempt %d/%d)",
                    validation.reason, current_model, attempt + 1, max_attempts,
                )
                next_model = cascade.get_next_model(current_model)
                if not next_model or attempt >= max_attempts - 1:
                    logger.warning("Cascade exhausted, returning last response from %s", current_model)
                    return response, cascade_attempts, judge_reason
                request.model = next_model
                cascade_attempts += 1
                continue

        # ── LLM-as-Judge (opt-in) ──
        if verifier in ("llm_judge", "rule_then_llm") and cascade.has_judge:
            # Extract prompt and response text for the judge
            last_msg = request.messages[-1].content if request.messages else ""
            prompt_for_judge = last_msg if isinstance(last_msg, str) else str(last_msg)
            resp_content = cascade._extract_content(response)
            judge_result = await cascade.judge_async(prompt_for_judge, resp_content)
            if not judge_result.passed:
                judge_reason = judge_result.reason
                logger.info(
                    "Judge FAIL [%s]: %s → escalating (attempt %d/%d)",
                    judge_reason, current_model, attempt + 1, max_attempts,
                )
                next_model = cascade.get_next_model(current_model)
                if not next_model or attempt >= max_attempts - 1:
                    logger.warning("Cascade exhausted, returning last response from %s", current_model)
                    return response, cascade_attempts, judge_reason
                request.model = next_model
                cascade_attempts += 1
                continue

        # All checks passed
        if not cascade.config.enabled:
            return response, cascade_attempts, judge_reason
        return response, cascade_attempts, judge_reason

    raise HTTPException(status_code=502, detail="All cascade attempts failed")


def _stream_response(
    proxy: UpstreamProxy,
    request: ChatCompletionRequest,
    provider_name: str | None,
    policy_name: str,
    method: str,
    score: float,
) -> StreamingResponse:
    """Return a streaming response with PolicyFlow headers."""
    return StreamingResponse(
        proxy.chat_completion_stream(request, provider_name=provider_name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-PolicyFlow-Policy": policy_name,
            "X-PolicyFlow-Method": method,
            "X-PolicyFlow-Score": f"{score:.3f}",
        },
    )


@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """List available models from upstream."""
    proxy: UpstreamProxy = app.state.proxy
    try:
        data = await proxy.list_models()
        return data
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
