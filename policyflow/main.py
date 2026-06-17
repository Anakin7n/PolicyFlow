"""PolicyFlow — FastAPI application entry point.

Week 1: OpenAI-compatible proxy that forwards requests upstream.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .config import Config
from .models import ChatCompletionRequest, ChatCompletionResponse, ModelsResponse
from .proxy import ProxyError, UpstreamProxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: initialize and clean up the upstream proxy client."""
    app.state.config = Config()
    app.state.proxy = UpstreamProxy(app.state.config)
    try:
        yield
    finally:
        await app.state.proxy.close()


app = FastAPI(
    title="PolicyFlow",
    description='策略路由中间件，给 one-api 装上「什么请求用什么模型」的大脑 — Week 1: proxy skeleton',
    version="0.1.0",
    lifespan=lifespan,
)


# ── OpenAI-compatible endpoints ──────────────────────────────────────

@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint.

    For now (Week 1), forwards directly upstream without policy routing.
    """
    proxy: UpstreamProxy = app.state.proxy

    try:
        if request.stream:
            return StreamingResponse(
                proxy.chat_completion_stream(request),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
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
