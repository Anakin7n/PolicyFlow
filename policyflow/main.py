"""PolicyFlow — FastAPI application entry point.

Week 4: SQLite logging + Dashboard + cost analysis.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import StreamingResponse

from . import db
from .cascade import CascadeConfig, CascadeValidator
from .config import Config
from .cost import calc_compared_cost, calc_cost
from .models import ChatCompletionRequest, ChatCompletionResponse, ModelsResponse
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
    cascade = CascadeValidator(CascadeConfig(**config.cascade_data))
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
    description='策略路由中间件，给 one-api 装上「什么请求用什么模型」的大脑 — Week 4: dashboard + costs',
    version="0.4.0",
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

    t_start = time.time()
    original_model = request.model
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
            return _stream_response(proxy, request, route_policy_name, route_method, route_score)
        else:
            response, cascade_attempts = await _forward_with_cascade(
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
        )
    except Exception as exc:
        logger.warning("Failed to log request: %s", exc)


async def _forward_with_cascade(
    proxy: UpstreamProxy,
    cascade: CascadeValidator,
    request: ChatCompletionRequest,
) -> tuple[ChatCompletionResponse, int]:
    """Forward request with cascade escalation on validation failure.

    Returns (response, cascade_attempts).
    """
    max_attempts = cascade.config.max_retries + 1
    cascade_attempts = 0

    for attempt in range(max_attempts):
        current_model = request.model

        try:
            response = await proxy.chat_completion(request)
        except ProxyError as e:
            logger.warning("Upstream error (attempt %d): %s", attempt + 1, e)
            next_model = cascade.get_next_model(current_model)
            if next_model and attempt < max_attempts - 1:
                request.model = next_model
                cascade_attempts += 1
                logger.info("Fallback: %s → %s", current_model, next_model)
                continue
            raise HTTPException(status_code=502, detail=str(e))

        # Cascade validation
        if not cascade.config.enabled:
            return response, cascade_attempts

        validation = cascade.validate(response, request)
        if validation.passed:
            return response, cascade_attempts

        logger.info(
            "Cascade fail [%s]: %s → escalating (attempt %d/%d)",
            validation.reason, current_model, attempt + 1, max_attempts,
        )

        next_model = cascade.get_next_model(current_model)
        if not next_model or attempt >= max_attempts - 1:
            logger.warning("Cascade exhausted, returning last response from %s", current_model)
            return response, cascade_attempts

        request.model = next_model
        cascade_attempts += 1

    raise HTTPException(status_code=502, detail="All cascade attempts failed")


def _stream_response(
    proxy: UpstreamProxy,
    request: ChatCompletionRequest,
    policy_name: str,
    method: str,
    score: float,
) -> StreamingResponse:
    """Return a streaming response with PolicyFlow headers."""
    return StreamingResponse(
        proxy.chat_completion_stream(request),
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
