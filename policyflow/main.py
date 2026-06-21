"""PolicyFlow — FastAPI application entry point.

Week 6: multi-provider routing + LLM-as-Judge cascade + CLI + AI optimizer.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import JSONResponse, StreamingResponse

from . import db
from .anthropic_adapter import (
    AnthropicStreamConverter,
    anthropic_to_chat_request,
    openai_to_anthropic_response,
)
from .db import hash_prompt
from .cascade import CascadeConfig, CascadeValidator
from .config import Config
from .cost import calc_compared_cost, calc_cost
from .models import ChatCompletionRequest, ChatCompletionResponse, Message, ModelsResponse
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
                jr, _ = await proxy.chat_completion_with_fallback(judge_req)
                text = (jr.choices[0].message.content or "").strip()
                passed = text.upper().startswith("PASS")
                reason = "" if passed else text[5:].strip()[:200] if text.startswith("FAIL") else text[:200]
                return passed, reason
            except Exception:
                return True, "judge_error"
        judge_fn = _judge
        cascade = CascadeValidator(cascade_config, judge_fn=judge_fn)

    await router.initialize()

    app.state.config = config
    app.state.proxy = proxy
    app.state.router = router
    app.state.cascade = cascade

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

@app.post("/v1/messages")
async def anthropic_messages(
    anthropic_body: dict[str, Any] = Body(...),
    fastapi_request: FastAPIRequest = None,
):
    """Anthropic Messages API endpoint — converts to OpenAI, routes, converts back.

    Supports both streaming (text only) and non-streaming.  Claude Code and
    other Anthropic-native clients point here::

        anthropic_base_url = "http://localhost:8000/v1"
    """
    router: Router = app.state.router
    proxy: UpstreamProxy = app.state.proxy
    cascade: CascadeValidator = app.state.cascade
    config: Config = app.state.config

    t_start = time.time()
    stream = anthropic_body.get("stream", False)
    original_model = anthropic_body.get("model", "claude-sonnet-4-6")

    # ── 1. Anthropic → OpenAI request ──────────────────────────
    openai_req = anthropic_to_chat_request(anthropic_body)

    # Extract prompt for logging (same logic as chat_completions)
    last_msg = openai_req.messages[-1].content if openai_req.messages else None
    if last_msg is None:
        prompt_text = ""
    elif isinstance(last_msg, str):
        prompt_text = last_msg
    else:
        prompt_text = str(last_msg)
    prompt_hash_val = hash_prompt(prompt_text) if prompt_text else ""
    prompt_preview_val = prompt_text[:500] if config.log_prompt_preview else ""
    judge_reason_val = ""
    user = fastapi_request.headers.get("X-User", "default")
    response = None
    cascade_attempts = 0
    success = True

    # ── 2. Route ───────────────────────────────────────────────
    decision = await router.route(openai_req)
    openai_req.model = decision.target_model
    route_method = decision.method
    route_score = decision.score
    route_policy_name = decision.policy.name if decision.policy else "none"

    logger.info(
        "Route [anthropic]: %s → %s  [%s, %.3f]",
        original_model, openai_req.model, route_method, route_score,
    )

    # ── 3. Forward (non-streaming only for cascade support) ────
    if stream:
        try:
            provider_name = proxy.config.get_model_provider(openai_req.model)
            converter = AnthropicStreamConverter(openai_req.model)
            sse_stream = _anthropic_stream_wrapper(
                proxy, openai_req, provider_name, converter,
            )
            log_params = {
                "user": user, "original_model": original_model,
                "routed_model": openai_req.model, "policy_name": route_policy_name,
                "method": route_method, "score": route_score,
                "success": success, "prompt_hash": prompt_hash_val,
                "prompt_preview": prompt_preview_val,
                "baseline_model": config.baseline_model,
            }
            wrapped = _stream_with_logging(sse_stream, log_params)
            return _stream_response_from_gen(wrapped, route_policy_name, route_method, route_score)
        except Exception:
            success = False
            raise
    else:
        try:
            cascade_specialty = decision.policy.name if decision.policy else ""
            response, cascade_attempts, judge_reason_val = await _try_with_capability_fallback(
                proxy, cascade, openai_req, decision, cascade_specialty, available_models,
            )
        except HTTPException:
            success = False
            raise
        except Exception:
            success = False
            raise
        finally:
            duration_ms = int((time.time() - t_start) * 1000)
            _log_to_db(
                user=user,
                original_model=original_model,
                routed_model=openai_req.model,
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
                baseline_model=config.baseline_model,
            )

        # Convert OpenAI response → Anthropic format
        anthropic_resp = openai_to_anthropic_response(
            response.model_dump(exclude_none=True),
            routed_model=openai_req.model,
        )
        return JSONResponse(
            content=anthropic_resp,
            headers={
                "X-PolicyFlow-Policy": route_policy_name,
                "X-PolicyFlow-Method": route_method,
                "X-PolicyFlow-Score": f"{route_score:.3f}",
                "X-PolicyFlow-Model": openai_req.model,
            },
        )


async def _anthropic_stream_wrapper(
    proxy: UpstreamProxy,
    request: ChatCompletionRequest,
    provider_name: str | None,
    converter: AnthropicStreamConverter,
) -> AsyncIterator[bytes]:
    """Bridge: OpenAI SSE stream → Anthropic SSE events."""
    async for chunk in proxy.chat_completion_stream(request, provider_name=provider_name):
        if isinstance(chunk, str):
            for event in converter.feed(chunk):
                yield event
        elif isinstance(chunk, bytes):
            for event in converter.feed(chunk.decode("utf-8")):
                yield event
    for event in converter.flush():
        yield event


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    fastapi_request: FastAPIRequest,
):
    """Chat completions with full pipeline: router → cascade → log."""
    router: Router = app.state.router
    proxy: UpstreamProxy = app.state.proxy
    cascade: CascadeValidator = app.state.cascade
    config: Config = app.state.config

    t_start = time.time()
    original_model = request.model

    # Hash the last user message for logging
    last_msg = request.messages[-1].content if request.messages else None
    if last_msg is None:
        prompt_text = ""
    elif isinstance(last_msg, str):
        prompt_text = last_msg
    else:
        prompt_text = str(last_msg)
    prompt_hash_val = hash_prompt(prompt_text) if prompt_text else ""
    prompt_preview_val = prompt_text[:500] if config.log_prompt_preview else ""
    judge_reason_val = ""
    user = fastapi_request.headers.get("X-User", "default")
    response = None  # Pre-bind for finally block safety
    cascade_attempts = 0
    success = True

    # ── Step 1: Route ──────────────────────────────────────────────
    decision = await router.route(request)
    request.model = decision.target_model
    route_method = decision.method
    route_score = decision.score
    route_policy_name = decision.policy.name if decision.policy else "none"

    logger.info(
        "Route: %s → %s  [%s, %.3f]",
        original_model, request.model, route_method, route_score,
    )

    # ── Step 2: Forward + cascade ──────────────────────────────────
    try:
        if request.stream:
            if cascade.config.enabled and cascade.config.verifier != "rule_only":
                logger.warning("Cascade validation is not supported for streaming requests")
            provider_name = proxy.config.get_model_provider(request.model)
            raw_stream = proxy.chat_completion_stream(request, provider_name=provider_name)
            log_params = {
                "user": user, "original_model": original_model,
                "routed_model": request.model, "policy_name": route_policy_name,
                "method": route_method, "score": route_score,
                "success": success, "prompt_hash": prompt_hash_val,
                "prompt_preview": prompt_preview_val,
                "baseline_model": config.baseline_model,
            }
            wrapped = _stream_with_logging(raw_stream, log_params)
            return _stream_response_from_gen(wrapped, route_policy_name, route_method, route_score)
        else:
            cascade_specialty = decision.policy.name if decision.policy else ""
            response, cascade_attempts, judge_reason_val = await _try_with_capability_fallback(
                proxy, cascade, request, decision, cascade_specialty, available_models,
            )
    except HTTPException:
        success = False
        raise
    except Exception:
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
            baseline_model=config.baseline_model,
        )

    # Return non-streaming response with PolicyFlow routing headers
    return JSONResponse(
        content=response.model_dump(exclude_none=True),
        headers={
            "X-PolicyFlow-Policy": route_policy_name,
            "X-PolicyFlow-Method": route_method,
            "X-PolicyFlow-Score": f"{route_score:.3f}",
        },
    )


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
    baseline_model: str = "deepseek-v4-pro",
) -> None:
    """Record the request and its costs to SQLite."""
    prompt_tokens = 0
    completion_tokens = 0

    if response and response.usage:
        prompt_tokens = response.usage.prompt_tokens
        completion_tokens = response.usage.completion_tokens

    estimated_cost = calc_cost(routed_model, prompt_tokens, completion_tokens)
    compared_cost = calc_compared_cost(prompt_tokens, completion_tokens, baseline_model)

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


async def _try_with_capability_fallback(
    proxy: UpstreamProxy,
    cascade: CascadeValidator,
    request: ChatCompletionRequest,
    decision,
    specialty: str,
    available_models: list[str],
) -> tuple[ChatCompletionResponse, int, str]:
    """Capability model failover: try top-N models by comprehensive scoring.

    For capability-routed requests, if the #1 model's providers are all down,
    transparently retry #2, #3, ... using the same weighted scoring.  Quality
    cascade is the final step on whichever model succeeds.

    For route_to requests: single call, no model retry.
    """
    if isinstance(decision.method, str) and decision.method.startswith("capability"):
        from .model_profiles import select_best_models
        cost_tier = getattr(decision.policy, "max_cost_tier", "") if decision.policy else ""
        thresholds = proxy.config.cost_tier_thresholds
        fallback = select_best_models(
            specialty, available_models, n=3,
            cost_tier=cost_tier, cost_tier_thresholds=thresholds,
        )
        if not fallback:
            fallback = [request.model]
    else:
        # route_to / hybrid-explicit: no model failover
        fallback = [request.model]

    last_err: Exception | None = None
    for model in fallback:
        request.model = model
        try:
            return await _forward_with_cascade(
                proxy, cascade, request, specialty, available_models,
            )
        except ProxyError as e:
            last_err = e
            logger.warning("Capability failover: %s failed, trying next in %s", model, fallback)
            continue
    raise HTTPException(status_code=502, detail=str(last_err or "all models exhausted"))


async def _forward_with_cascade(
    proxy: UpstreamProxy,
    cascade: CascadeValidator,
    request: ChatCompletionRequest,
    specialty: str = "",
    available_models: list[str] | None = None,
) -> tuple[ChatCompletionResponse, int, str]:
    """Forward request and run quality-cascade validation.

    Quality cascade only: validates the response and escalates on quality
    failure.  Proxy errors are NOT handled here — model failover (capability
    top-N retry) and provider fallback (upstream.fallback_model) are the
    caller's / proxy layer's responsibility.

    Returns (response, cascade_attempts, judge_reason).
    """
    max_attempts = cascade.config.max_retries + 1
    cascade_attempts = 0
    judge_reason = ""
    verifier = cascade.config.verifier

    # Quality cascade disabled → forward once, no validation
    if not cascade.config.enabled:
        response, _ = await proxy.chat_completion_with_fallback(request)
        return response, 0, ""

    for attempt in range(max_attempts):
        current_model = request.model

        try:
            response, _ = await proxy.chat_completion_with_fallback(request)
        except ProxyError:
            raise  # model failover belongs to the caller, not here

        # ── Rule-based validation (skip if verifier is llm_judge-only) ──
        if verifier != "llm_judge":
            validation = cascade.validate(response, request)
            if not validation.passed:
                logger.info(
                    "Cascade fail [%s]: %s → escalating (attempt %d/%d)",
                    validation.reason, current_model, attempt + 1, max_attempts,
                )
                next_model = cascade.get_next_model(current_model, specialty, available_models)
                if not next_model or attempt >= max_attempts - 1:
                    logger.warning("Cascade exhausted, returning last response from %s", current_model)
                    return response, cascade_attempts, judge_reason
                request.model = next_model
                cascade_attempts += 1
                continue

        # ── LLM-as-Judge (opt-in) ──
        if verifier in ("llm_judge", "rule_then_llm") and cascade.has_judge:
            # Extract prompt and response text for the judge
            last_msg = request.messages[-1].content if request.messages else None
            if last_msg is None:
                prompt_for_judge = ""
            elif isinstance(last_msg, str):
                prompt_for_judge = last_msg
            else:
                prompt_for_judge = str(last_msg)
            if not response.choices:
                return response, cascade_attempts, "judge_no_choices"
            resp_content = cascade._extract_content(response)
            judge_result = await cascade.judge_async(prompt_for_judge, resp_content)
            if not judge_result.passed:
                judge_reason = judge_result.reason
                logger.info(
                    "Judge FAIL [%s]: %s → escalating (attempt %d/%d)",
                    judge_reason, current_model, attempt + 1, max_attempts,
                )
                next_model = cascade.get_next_model(current_model, specialty, available_models)
                if not next_model or attempt >= max_attempts - 1:
                    logger.warning("Cascade exhausted, returning last response from %s", current_model)
                    return response, cascade_attempts, judge_reason
                request.model = next_model
                cascade_attempts += 1
                continue

        # All checks passed
        return response, cascade_attempts, judge_reason

    raise HTTPException(status_code=502, detail="All cascade attempts failed")


async def _stream_with_logging(
    stream: AsyncIterator[bytes],
    log_params: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Wrap a raw SSE stream: yield chunks, log to DB after stream ends."""
    duration_start = time.time()
    final_usage: dict[str, int] = {}
    try:
        async for chunk in stream:
            yield chunk
            # Collect usage from the last SSE data chunk (OpenAI puts usage there)
            if isinstance(chunk, bytes):
                try:
                    text = chunk.decode("utf-8", errors="replace")
                    if text.startswith("data: ") and '"usage":' in text:
                        import json
                        payload = json.loads(text[6:].strip())
                        u = payload.get("usage", {})
                        if u:
                            final_usage = {
                                "prompt_tokens": u.get("prompt_tokens", 0),
                                "completion_tokens": u.get("completion_tokens", 0),
                            }
                except Exception:
                    pass
    finally:
        duration_ms = int((time.time() - duration_start) * 1000)
        try:
            prompt_tokens = final_usage.get("prompt_tokens", 0)
            completion_tokens = final_usage.get("completion_tokens", 0)
            estimated_cost = calc_cost(
                log_params["routed_model"], prompt_tokens, completion_tokens,
            )
            compared_cost = calc_compared_cost(
                prompt_tokens, completion_tokens,
                log_params.get("baseline_model", "deepseek-v4-pro"),
            )
            db.log_request(
                user=log_params.get("user", "default"),
                original_model=log_params.get("original_model", ""),
                routed_model=log_params.get("routed_model", ""),
                policy_name=log_params.get("policy_name", ""),
                method=log_params.get("method", ""),
                similarity_score=log_params.get("score", 0.0),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost=estimated_cost,
                compared_cost=compared_cost,
                cascade_attempts=0,
                duration_ms=duration_ms,
                success=log_params.get("success", True),
                prompt_hash=log_params.get("prompt_hash", ""),
                prompt_preview=log_params.get("prompt_preview", ""),
                judge_reason="",
            )
        except Exception as exc:
            logger.warning("Failed to log streaming request: %s", exc)


def _stream_response_from_gen(
    stream: AsyncIterator[bytes],
    policy_name: str,
    method: str,
    score: float,
) -> StreamingResponse:
    """Return a StreamingResponse from an already-constructed async generator."""
    return StreamingResponse(
        stream,
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
