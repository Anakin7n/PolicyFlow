"""PolicyFlow — FastAPI application entry point.

Week 2: Policy engine + embedding classifier + routing decisions.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse, ModelsResponse
from .proxy import ProxyError, UpstreamProxy
from .router import Router

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init config, proxy, router. Shutdown: clean up connections."""
    config = Config()
    proxy = UpstreamProxy(config)
    router = Router(config)
    await router.initialize()

    app.state.config = config
    app.state.proxy = proxy
    app.state.router = router

    try:
        yield
    finally:
        await router.close()
        await proxy.close()


app = FastAPI(
    title="PolicyFlow",
    description='策略路由中间件，给 one-api 装上「什么请求用什么模型」的大脑 — Week 2: policy routing',
    version="0.2.0",
    lifespan=lifespan,
)


# ── OpenAI-compatible endpoints ──────────────────────────────────────

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint.

    Week 2: Policy routing — classifies the request and rewrites the model
    before forwarding upstream.
    """
    router: Router = app.state.router
    proxy: UpstreamProxy = app.state.proxy

    # ── Policy routing ────────────────────────────────────────────
    decision = await router.route(request)
    original_model = request.model
    request.model = decision.target_model
    logger.info(
        "Route: %s → %s  [%s, %.3f]",
        original_model, decision.target_model, decision.method, decision.score,
    )

    try:
        if request.stream:
            return StreamingResponse(
                proxy.chat_completion_stream(request),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "X-PolicyFlow-Policy": decision.policy.name if decision.policy else "none",
                    "X-PolicyFlow-Method": decision.method,
                    "X-PolicyFlow-Score": f"{decision.score:.3f}",
                },
            )
        return await proxy.chat_completion(request)
    except ProxyError as e:
        raise HTTPException(status_code=502, detail=str(e))


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
    return {"status": "ok", "version": "0.1.0"}
