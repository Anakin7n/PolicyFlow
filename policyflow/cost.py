"""Model pricing data and cost calculation.

Prices per 1M tokens (input / output) in USD.
Domestic Chinese models (CNY) converted at ~7.2 CNY/USD where int'l pricing unavailable.

⚠️  PRICE DATA DISCLAIMER  ──────────────────────────────────────────
The numbers below were collected against vendor pricing pages on the
date stamped in the source list, but LLM pricing changes frequently
and some model IDs in this file may be vendor-specific or forward-
looking placeholders that do not match a model you can actually call.

Before relying on cost reports for billing decisions:
  1. Cross-check rates with each vendor's current pricing page
  2. Treat unknown model IDs as the (1.00, 1.00) USD/M fallback (see
     `_lookup_price`) — they will still log, but the dollar figures
     are nominal
  3. To override a price without editing source, file an issue or
     monkey-patch MODEL_PRICES at startup

Sources (2026-06):
  anthropic.com/pricing, openai.com/api/pricing, ai.google.dev/pricing,
  api-docs.deepseek.com, help.aliyun.com/zh/model-studio/model-pricing,
  open.bigmodel.cn, platform.moonshot.cn, cloud.baidu.com/doc/qianfan,
  volcengine.com (火山引擎)
"""

from __future__ import annotations

# Pricing: (input_price_per_1M, output_price_per_1M) in USD
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # ── Anthropic ─────────────────────────────────────────────
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),

    # ── OpenAI ────────────────────────────────────────────────
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o3-mini": (1.10, 4.40),

    # ── Google Gemini ─────────────────────────────────────────
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro": (2.00, 12.00),

    # ── DeepSeek ──────────────────────────────────────────────
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v3": (0.28, 0.42),
    "deepseek-r1": (0.55, 2.19),

    # ── 阿里通义千问 Qwen ──────────────────────────────────────
    "qwen-max": (2.50, 7.50),
    "qwen-plus": (0.60, 3.60),
    "qwen-flash": (0.10, 0.40),
    "qwen3-235b-a22b": (0.84, 3.36),
    "qwen-vl-plus": (0.24, 1.92),

    # ── 智谱 GLM ──────────────────────────────────────────────
    "glm-5.2": (1.40, 4.40),
    "glm-5": (1.00, 3.20),
    "glm-5.1": (0.98, 3.08),

    # ── 月之暗面 Kimi ──────────────────────────────────────────
    "kimi-k2.6": (0.90, 3.75),

    # ── 字节豆包 Doubao (¥ → ¥, ~7.2 rate) ────────────────────
    "doubao-1.6": (0.11, 1.11),
    "doubao-seed-2.0-lite": (0.08, 0.50),

    # ── 百度文心 ERNIE (¥ → ¥, ~7.2 rate) ─────────────────────
    "ernie-5.1": (0.56, 2.50),
    "ernie-4.5-turbo": (0.11, 0.44),
    "ernie-speed-pro": (0.04, 0.08),
}


def get_price(model_id: str, tokens: int, is_input: bool = True) -> float:
    """Get the cost for a given number of tokens."""
    prices = _lookup_price(model_id)
    price_per_1m = prices[0] if is_input else prices[1]
    return (tokens / 1_000_000) * price_per_1m


def calc_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate total cost for a request."""
    input_cost = get_price(model_id, prompt_tokens, is_input=True)
    output_cost = get_price(model_id, completion_tokens, is_input=False)
    return round(input_cost + output_cost, 6)


def calc_compared_cost(
    prompt_tokens: int, completion_tokens: int, compared_model: str = "deepseek-v4-pro"
) -> float:
    """Calculate what the cost would have been if routed to the comparison model.

    Default baseline: deepseek-v4-pro — a popular domestic model with moderate pricing.
    """
    return calc_cost(compared_model, prompt_tokens, completion_tokens)


def _lookup_price(model_id: str) -> tuple[float, float]:
    """Look up pricing. Returns (1.00, 1.00) as fallback for unknown models.

    Matches by exact key first, then by longest prefix match.
    """
    if model_id in MODEL_PRICES:
        return MODEL_PRICES[model_id]
    for prefix in sorted(MODEL_PRICES.keys(), key=len, reverse=True):
        if model_id.startswith(prefix):
            return MODEL_PRICES[prefix]
    return (1.00, 1.00)  # unknown model default
