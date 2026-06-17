"""Model pricing data and cost calculation.

Prices per 1M tokens (input / output). Updated 2026-06.
"""

from __future__ import annotations

# Pricing: (input_price_per_1M, output_price_per_1M) in USD
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-8": (15.00, 75.00),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "o1": (15.00, 60.00),
    "o3-mini": (1.10, 4.40),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    # DeepSeek
    "deepseek-v3": (0.27, 1.10),
    "deepseek-r1": (0.55, 2.19),
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
    prompt_tokens: int, completion_tokens: int, compared_model: str = "claude-opus-4-8"
) -> float:
    """Calculate what the cost would have been if routed to the comparison model."""
    return calc_cost(compared_model, prompt_tokens, completion_tokens)


def _lookup_price(model_id: str) -> tuple[float, float]:
    """Look up pricing. Returns (1.00, 1.00) as fallback for unknown models."""
    if model_id in MODEL_PRICES:
        return MODEL_PRICES[model_id]
    for prefix in sorted(MODEL_PRICES.keys(), key=len, reverse=True):
        if model_id.startswith(prefix):
            return MODEL_PRICES[prefix]
    return (1.00, 1.00)  # unknown model default
