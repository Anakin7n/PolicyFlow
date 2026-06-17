"""PolicyFlow — FastAPI application entry point.

Week 3: Cascade validator + smart modifiers (agent/reasoning/window/session/fallback).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import StreamingResponse

from .cascade import CascadeConfig, CascadeValidator
from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse, ModelsResponse
from .modifiers import ModifierEngine
from .proxy import ProxyError, UpstreamProxy
from .router import Router

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init all components. Shutdown: clean up connections."""
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

    try:
        yield
    finally:
        await router.close()
        await proxy.close()


app = FastAPI(
    title="PolicyFlow",
    description='策略路由中间件，给 one-api 装上「什么请求用什么模型」的大脑 — Week 3: cascade + modifiers',
    version="0.3.0",
    lifespan=lifespan,
)


# ── OpenAI-compatible endpoints ──────────────────────────────────────

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    fastapi_request: FastAPIRequest,
):
    """Chat completions with full policy routing, modifiers, and cascade validation.

    Flow:
    1. Modifiers (agent/reasoning/session/context-window) → may override model
    2. Policy router (keywords + embedding) → pick target model
    3. Forward upstream
    4. Cascade validate → if cheap model failed, escalate and retry
    """
    router: Router = app.state.router
    proxy: UpstreamProxy = app.state.proxy
    cascade: CascadeValidator = app.state.cascade
    modifiers: ModifierEngine = app.state.modifiers

    original_model = request.model
    session_id = fastapi_request.headers.get("X-Session-ID")

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
    if request.stream:
        # Streaming: skip cascade (can't validate mid-stream)
        return _stream_response(proxy, request, route_policy_name, route_method, route_score)

    return await _forward_with_cascade(
        proxy, cascade, request, route_policy_name, route_method, route_score
    )


async def _forward_with_cascade(
    proxy: UpstreamProxy,
    cascade: CascadeValidator,
    request: ChatCompletionRequest,
    policy_name: str,
    method: str,
    score: float,
) -> ChatCompletionResponse:
    """Forward request with cascade escalation on validation failure."""
    max_attempts = cascade.config.max_retries + 1
    last_error: str | None = None

    for attempt in range(max_attempts):
        current_model = request.model

        # Try forwarding
        try:
            response = await proxy.chat_completion(request)
        except ProxyError as e:
            last_error = str(e)
            logger.warning("Upstream error (attempt %d): %s", attempt + 1, e)
            # Fallback: try next model on connection/HTTP error
            next_model = cascade.get_next_model(current_model)
            if next_model and attempt < max_attempts - 1:
                request.model = next_model
                logger.info("Fallback: %s → %s", current_model, next_model)
                continue
            raise HTTPException(status_code=502, detail=last_error or "Upstream error")

        # Cascade validation (only for cascade-enabled policies)
        if not cascade.config.enabled:
            return response

        validation = cascade.validate(response, request)
        if validation.passed:
            return response

        logger.info(
            "Cascade fail [%s]: %s → escalating (attempt %d/%d)",
            validation.reason, current_model, attempt + 1, max_attempts,
        )

        next_model = cascade.get_next_model(current_model)
        if not next_model or attempt >= max_attempts - 1:
            # No more escalation options — return the response as-is
            logger.warning("Cascade exhausted, returning last response from %s", current_model)
            return response

        request.model = next_model
        method = "cascade"
        policy_name = f"cascade:{current_model}"

    # Should never reach here, but type checker needs this
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
