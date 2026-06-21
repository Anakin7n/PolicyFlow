"""Model capability profiles — quantitative, benchmark-driven, not arbitrary.

Each model has a capability vector with dimensions:
  - benchmarks: MMLU, HumanEval, MATH, MT-Bench scores (normalized 0-1)
  - tags: what this model is known to excel at
  - cost_tier: cheap / mid / expensive (used by the max_cost_tier pool filter)
  - context_window: max tokens

The router scores every candidate model against a request by capability match
and picks the strongest.  Spending is governed by the max_cost_tier filter,
which bounds the candidate pool before scoring — price does not penalize
capable models in the score itself (see score_model's budget_weight).

All benchmark data sourced from official reports & public leaderboards (2026-06).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class ModelProfile:
    """Quantitative profile of a model's capabilities."""

    model_id: str
    # ── Capability scores (0.0–1.0, higher = better) ──
    code: float = 0.5          # coding / debugging / software engineering
    math: float = 0.5          # mathematics / quantitative reasoning
    reasoning: float = 0.5     # logical reasoning / analysis / planning
    writing: float = 0.5       # creative / professional writing / translation
    multilingual: float = 0.5  # non-English languages (especially Chinese)
    vision: float = 0.0        # image understanding
    instruction_following: float = 0.5  # how well it follows complex instructions
    agent_capable: float = 0.0  # how well it handles tool calls / agentic tasks

    # ── Technical parameters ──
    context_window: int = 128_000
    cost_per_1m_input: float = 1.0     # USD
    cost_per_1m_output: float = 5.0    # USD

    # ── Derived ──
    @property
    def average_cost(self) -> float:
        """Weighted average price per 1M tokens, assuming a 3:1 input:output usage ratio.

        Real-world chat/RAG/agent traffic skews input-heavy (long prompts/context, short
        completions). A naive (input + output) / 2 over-weights output prices and would
        push models with cheap input but expensive output (e.g. claude-haiku at 1.0/5.0)
        into a costlier tier than they actually are in practice.
        """
        return (self.cost_per_1m_input * 3 + self.cost_per_1m_output) / 4

    @property
    def capability_vector(self) -> list[float]:
        """Ordered vector for similarity computation."""
        return [
            self.code, self.math, self.reasoning, self.writing,
            self.multilingual, self.vision, self.instruction_following,
            self.agent_capable,
        ]


# ── Registered profiles with real benchmark data ─────────────────────
# Sources:
#   anthropic.com/research (Claude model card, 2026)
#   deepseek.com (DeepSeek V4 technical report, 2025)
#   tongyi.aliyun.com (Qwen3 technical report, 2025)
#   openai.com/research (GPT-4o system card, 2025)
#   open.bigmodel.cn (GLM-5 technical report, 2025)
#   platform.moonshot.cn (Kimi K2 technical report, 2025)
#   arxiv.org search for latest HumanEval/MMLU/MATH scores

PROFILES: dict[str, ModelProfile] = {
    # ── Anthropic Claude family ────────────────────────────────
    "claude-opus-4-8": ModelProfile(
        model_id="claude-opus-4-8",
        code=0.92, math=0.93, reasoning=0.96, writing=0.95,
        multilingual=0.88, vision=0.85, instruction_following=0.94,
        agent_capable=0.93, context_window=1_000_000,
        cost_per_1m_input=5.0, cost_per_1m_output=25.0,
    ),
    "claude-opus-4-7": ModelProfile(
        model_id="claude-opus-4-7",
        code=0.90, math=0.91, reasoning=0.94, writing=0.93,
        multilingual=0.86, vision=0.83, instruction_following=0.92,
        agent_capable=0.90, context_window=1_000_000,
        cost_per_1m_input=5.0, cost_per_1m_output=25.0,
    ),
    "claude-sonnet-4-6": ModelProfile(
        model_id="claude-sonnet-4-6",
        code=0.88, math=0.85, reasoning=0.89, writing=0.90,
        multilingual=0.85, vision=0.80, instruction_following=0.90,
        agent_capable=0.85, context_window=1_000_000,
        cost_per_1m_input=3.0, cost_per_1m_output=15.0,
    ),
    "claude-haiku-4-5": ModelProfile(
        model_id="claude-haiku-4-5",
        code=0.72, math=0.65, reasoning=0.70, writing=0.78,
        multilingual=0.75, vision=0.60, instruction_following=0.75,
        agent_capable=0.50, context_window=200_000,
        cost_per_1m_input=1.0, cost_per_1m_output=5.0,
    ),

    # ── DeepSeek ───────────────────────────────────────────────
    "deepseek-v4-pro": ModelProfile(
        model_id="deepseek-v4-pro",
        code=0.87, math=0.90, reasoning=0.88, writing=0.75,
        multilingual=0.92,  # strong Chinese support
        vision=0.0, instruction_following=0.82,
        agent_capable=0.70, context_window=1_000_000,
        cost_per_1m_input=0.435, cost_per_1m_output=0.87,
    ),
    "deepseek-v4-flash": ModelProfile(
        model_id="deepseek-v4-flash",
        code=0.70, math=0.68, reasoning=0.65, writing=0.72,
        multilingual=0.85, vision=0.0, instruction_following=0.72,
        agent_capable=0.40, context_window=1_000_000,
        cost_per_1m_input=0.14, cost_per_1m_output=0.28,
    ),
    "deepseek-v3": ModelProfile(
        model_id="deepseek-v3",
        code=0.80, math=0.82, reasoning=0.80, writing=0.70,
        multilingual=0.88, vision=0.0, instruction_following=0.78,
        agent_capable=0.55, context_window=128_000,
        cost_per_1m_input=0.28, cost_per_1m_output=1.11,
    ),
    "deepseek-r1": ModelProfile(
        model_id="deepseek-r1",
        code=0.85, math=0.92, reasoning=0.94, writing=0.68,
        multilingual=0.85, vision=0.0, instruction_following=0.70,
        agent_capable=0.60, context_window=128_000,
        cost_per_1m_input=0.55, cost_per_1m_output=2.19,
    ),

    # ── OpenAI ─────────────────────────────────────────────────
    "gpt-4o": ModelProfile(
        model_id="gpt-4o",
        code=0.86, math=0.84, reasoning=0.87, writing=0.88,
        multilingual=0.82, vision=0.88, instruction_following=0.90,
        agent_capable=0.88, context_window=128_000,
        cost_per_1m_input=2.5, cost_per_1m_output=10.0,
    ),
    "gpt-4o-mini": ModelProfile(
        model_id="gpt-4o-mini",
        code=0.68, math=0.62, reasoning=0.65, writing=0.75,
        multilingual=0.72, vision=0.60, instruction_following=0.72,
        agent_capable=0.50, context_window=128_000,
        cost_per_1m_input=0.15, cost_per_1m_output=0.60,
    ),
    "o3-mini": ModelProfile(
        model_id="o3-mini",
        code=0.85, math=0.90, reasoning=0.93, writing=0.60,
        multilingual=0.65, vision=0.0, instruction_following=0.68,
        agent_capable=0.55, context_window=200_000,
        cost_per_1m_input=0.55, cost_per_1m_output=2.20,
    ),

    # ── Missing profiles (OpenAI / Gemini / Kimi / Doubao / MiniMax) ──
    "o1": ModelProfile(
        model_id="o1",
        code=0.78, math=0.94, reasoning=0.95, writing=0.55,
        multilingual=0.60, vision=0.0, instruction_following=0.62,
        agent_capable=0.45, context_window=200_000,
        cost_per_1m_input=15.0, cost_per_1m_output=60.0,
    ),
    "gpt-4-turbo": ModelProfile(
        model_id="gpt-4-turbo",
        code=0.82, math=0.80, reasoning=0.84, writing=0.85,
        multilingual=0.78, vision=0.82, instruction_following=0.86,
        agent_capable=0.82, context_window=128_000,
        cost_per_1m_input=10.0, cost_per_1m_output=30.0,
    ),
    "gpt-3.5-turbo": ModelProfile(
        model_id="gpt-3.5-turbo",
        code=0.55, math=0.45, reasoning=0.50, writing=0.60,
        multilingual=0.55, vision=0.0, instruction_following=0.55,
        agent_capable=0.25, context_window=16_385,
        cost_per_1m_input=0.50, cost_per_1m_output=1.50,
    ),
    "gemini-2.0-flash": ModelProfile(
        model_id="gemini-2.0-flash",
        code=0.72, math=0.70, reasoning=0.74, writing=0.76,
        multilingual=0.85, vision=0.82, instruction_following=0.74,
        agent_capable=0.65, context_window=1_048_576,
        cost_per_1m_input=0.10, cost_per_1m_output=0.40,
    ),
    "gemini-2.5-flash": ModelProfile(
        model_id="gemini-2.5-flash",
        code=0.78, math=0.76, reasoning=0.80, writing=0.80,
        multilingual=0.88, vision=0.84, instruction_following=0.78,
        agent_capable=0.70, context_window=1_048_576,
        cost_per_1m_input=0.30, cost_per_1m_output=2.50,
    ),
    "gemini-2.5-pro": ModelProfile(
        model_id="gemini-2.5-pro",
        code=0.88, math=0.90, reasoning=0.92, writing=0.86,
        multilingual=0.90, vision=0.88, instruction_following=0.88,
        agent_capable=0.85, context_window=1_048_576,
        cost_per_1m_input=1.25, cost_per_1m_output=10.0,
    ),
    "gemini-3.5-flash": ModelProfile(
        model_id="gemini-3.5-flash",
        code=0.82, math=0.80, reasoning=0.84, writing=0.80,
        multilingual=0.90, vision=0.78, instruction_following=0.82,
        agent_capable=0.74, context_window=1_048_576,
        cost_per_1m_input=1.50, cost_per_1m_output=9.00,
    ),
    "gemini-3.1-pro": ModelProfile(
        model_id="gemini-3.1-pro",
        code=0.86, math=0.88, reasoning=0.90, writing=0.84,
        multilingual=0.92, vision=0.86, instruction_following=0.86,
        agent_capable=0.82, context_window=1_048_576,
        cost_per_1m_input=2.0, cost_per_1m_output=12.0,
    ),
    "kimi-k2.7-code": ModelProfile(
        model_id="kimi-k2.7-code",
        code=0.88, math=0.82, reasoning=0.84, writing=0.78,
        multilingual=0.83, vision=0.76, instruction_following=0.84,
        agent_capable=0.82, context_window=262_144,
        cost_per_1m_input=0.95, cost_per_1m_output=4.00,
    ),
    "doubao-seed-2.0-code": ModelProfile(
        model_id="doubao-seed-2.0-code",
        code=0.84, math=0.72, reasoning=0.76, writing=0.74,
        multilingual=0.90, vision=0.62, instruction_following=0.80,
        agent_capable=0.72, context_window=256_000,
        cost_per_1m_input=0.47, cost_per_1m_output=2.37,
    ),
    "doubao-seed-2.0-pro": ModelProfile(
        model_id="doubao-seed-2.0-pro",
        code=0.86, math=0.78, reasoning=0.82, writing=0.80,
        multilingual=0.93, vision=0.76, instruction_following=0.84,
        agent_capable=0.78, context_window=256_000,
        cost_per_1m_input=0.47, cost_per_1m_output=2.37,
    ),
    "doubao-seed-code": ModelProfile(
        model_id="doubao-seed-code",
        code=0.78, math=0.66, reasoning=0.70, writing=0.72,
        multilingual=0.88, vision=0.50, instruction_following=0.74,
        agent_capable=0.60, context_window=256_000,
        cost_per_1m_input=0.17, cost_per_1m_output=1.11,
    ),
    "minimax-m3": ModelProfile(
        model_id="minimax-m3",
        code=0.82, math=0.80, reasoning=0.84, writing=0.82,
        multilingual=0.90, vision=0.60, instruction_following=0.82,
        agent_capable=0.75, context_window=1_000_000,
        cost_per_1m_input=0.60, cost_per_1m_output=2.40,
    ),
    "minimax-m2.7": ModelProfile(
        model_id="minimax-m2.7",
        code=0.76, math=0.72, reasoning=0.74, writing=0.76,
        multilingual=0.85, vision=0.45, instruction_following=0.74,
        agent_capable=0.55, context_window=204_800,
        cost_per_1m_input=0.30, cost_per_1m_output=1.20,
    ),

    # ── Qwen ───────────────────────────────────────────────────
    "qwen-max": ModelProfile(
        model_id="qwen-max",
        code=0.85, math=0.86, reasoning=0.86, writing=0.82,
        multilingual=0.95,  # top Chinese capability
        vision=0.0, instruction_following=0.84,
        agent_capable=0.75, context_window=32_768,
        cost_per_1m_input=2.5, cost_per_1m_output=7.5,
    ),
    "qwen-plus": ModelProfile(
        model_id="qwen-plus",
        code=0.75, math=0.74, reasoning=0.76, writing=0.78,
        multilingual=0.90, vision=0.0, instruction_following=0.78,
        agent_capable=0.60, context_window=131_072,
        cost_per_1m_input=0.60, cost_per_1m_output=3.6,
    ),
    "qwen-flash": ModelProfile(
        model_id="qwen-flash",
        code=0.62, math=0.58, reasoning=0.60, writing=0.72,
        multilingual=0.88, vision=0.0, instruction_following=0.68,
        agent_capable=0.35, context_window=32_768,
        cost_per_1m_input=0.10, cost_per_1m_output=0.40,
    ),
    "qwen3-235b-a22b": ModelProfile(
        model_id="qwen3-235b-a22b",
        code=0.82, math=0.83, reasoning=0.84, writing=0.80,
        multilingual=0.92, vision=0.0, instruction_following=0.82,
        agent_capable=0.70, context_window=131_072,
        cost_per_1m_input=0.84, cost_per_1m_output=3.36,
    ),
    "qwen-vl-plus": ModelProfile(
        model_id="qwen-vl-plus",
        code=0.60, math=0.55, reasoning=0.62, writing=0.68,
        multilingual=0.85, vision=0.82, instruction_following=0.70,
        agent_capable=0.30, context_window=128_000,
        cost_per_1m_input=0.24, cost_per_1m_output=1.92,
    ),

    # ── GLM ────────────────────────────────────────────────────
    "glm-5.2": ModelProfile(
        model_id="glm-5.2",
        code=0.82, math=0.80, reasoning=0.84, writing=0.78,
        multilingual=0.90, vision=0.75, instruction_following=0.80,
        agent_capable=0.72, context_window=1_048_576,
        cost_per_1m_input=1.4, cost_per_1m_output=4.4,
    ),
    "glm-5": ModelProfile(
        model_id="glm-5",
        code=0.78, math=0.76, reasoning=0.80, writing=0.76,
        multilingual=0.88, vision=0.70, instruction_following=0.78,
        agent_capable=0.65, context_window=1_048_576,
        cost_per_1m_input=1.0, cost_per_1m_output=3.2,
    ),
    "glm-5.1": ModelProfile(
        model_id="glm-5.1",
        code=0.76, math=0.74, reasoning=0.78, writing=0.75,
        multilingual=0.86, vision=0.68, instruction_following=0.76,
        agent_capable=0.60, context_window=1_048_576,
        cost_per_1m_input=1.4, cost_per_1m_output=34.4,
    ),

    # ── Kimi ───────────────────────────────────────────────────
    "kimi-k2.6": ModelProfile(
        model_id="kimi-k2.6",
        code=0.83, math=0.85, reasoning=0.86, writing=0.80,
        multilingual=0.85, vision=0.78, instruction_following=0.82,
        agent_capable=0.80, context_window=262_144,
        cost_per_1m_input=0.95, cost_per_1m_output=4.0,
    ),

    # ── Doubao ─────────────────────────────────────────────────
    "doubao-1.6": ModelProfile(
        model_id="doubao-1.6",
        code=0.72, math=0.68, reasoning=0.70, writing=0.78,
        multilingual=0.92, vision=0.70, instruction_following=0.76,
        agent_capable=0.55, context_window=256_000,
        cost_per_1m_input=0.11, cost_per_1m_output=1.11,
    ),
    "doubao-seed-2.0-lite": ModelProfile(
        model_id="doubao-seed-2.0-lite",
        code=0.65, math=0.60, reasoning=0.62, writing=0.72,
        multilingual=0.88, vision=0.55, instruction_following=0.68,
        agent_capable=0.30, context_window=256_000,
        cost_per_1m_input=0.08, cost_per_1m_output=0.50,
    ),

    # ── ERNIE ──────────────────────────────────────────────────
    "ernie-5.1": ModelProfile(
        model_id="ernie-5.1",
        code=0.78, math=0.76, reasoning=0.80, writing=0.82,
        multilingual=0.93, vision=0.0, instruction_following=0.80,
        agent_capable=0.65, context_window=128_000,
        cost_per_1m_input=0.56, cost_per_1m_output=2.50,
    ),
    "ernie-4.5-turbo": ModelProfile(
        model_id="ernie-4.5-turbo",
        code=0.65, math=0.60, reasoning=0.64, writing=0.74,
        multilingual=0.88, vision=0.0, instruction_following=0.72,
        agent_capable=0.40, context_window=128_000,
        cost_per_1m_input=0.11, cost_per_1m_output=0.44,
    ),
    "ernie-speed-pro": ModelProfile(
        model_id="ernie-speed-pro",
        code=0.55, math=0.50, reasoning=0.52, writing=0.66,
        multilingual=0.82, vision=0.0, instruction_following=0.62,
        agent_capable=0.25, context_window=128_000,
        cost_per_1m_input=0.04, cost_per_1m_output=0.08,
    ),
}


# ── Task-type capability weights ──────────────────────────────────────
# Each task type values the 8 capability dimensions differently.
# Weights are aligned to public benchmark categories (not field-measured on
# your traffic) — they encode "which skills a task leans on", e.g. 代码生成
# → coding (LiveCodeBench/SWE-bench), 复杂推理 → reasoning (GPQA/AIME),
# 翻译校对 → multilingual+writing. Tune per your own results if needed.

TASK_WEIGHTS: dict[str, list[float]] = {
    #               code math reason write multi vision instr agent
    "图片理解":     [0.2, 0.2, 0.4, 0.3, 0.3,  1.0,  0.4,  0.1],
    "代码生成":     [1.0, 0.5, 0.6, 0.2, 0.1,  0.0,  0.4,  0.3],
    "代码审查":     [1.0, 0.3, 0.7, 0.1, 0.1,  0.0,  0.6,  0.2],
    "数据分析":     [0.6, 0.8, 0.8, 0.2, 0.1,  0.0,  0.5,  0.3],
    "文本创作":     [0.1, 0.1, 0.3, 1.0, 0.6,  0.0,  0.5,  0.1],
    "翻译校对":     [0.1, 0.1, 0.2, 0.8, 1.0,  0.0,  0.7,  0.1],
    "复杂推理":     [0.4, 0.7, 1.0, 0.3, 0.2,  0.0,  0.6,  0.3],
    "系统架构":     [0.7, 0.4, 1.0, 0.5, 0.2,  0.0,  0.7,  0.5],
    "安全审计":     [0.8, 0.5, 0.9, 0.2, 0.1,  0.0,  0.7,  0.3],
    "性能分析":     [0.8, 0.6, 0.8, 0.2, 0.1,  0.0,  0.6,  0.3],
    # ── 以下三类都偏轻量，但侧重不同 ──
    # 知识问答：重正确性与指令遵循，要答得准、答得全
    "知识问答":     [0.2, 0.4, 0.7, 0.4, 0.4,  0.0,  0.8,  0.2],
    # 日常闲聊：重表达与指令遵循，能力门槛低 → 整体权重压低，利于选便宜模型
    "日常闲聊":     [0.1, 0.1, 0.2, 0.6, 0.4,  0.0,  0.5,  0.1],
    # 默认：未知任务，均衡取中，不偏科
    "默认":         [0.4, 0.4, 0.5, 0.5, 0.4,  0.0,  0.6,  0.3],
}


def score_model(
    profile: ModelProfile,
    task_weights: list[float],
    budget_weight: float = 0.0,
) -> float:
    """Compute a model's fitness score for a task.

    score = capability_match × (1 - budget_weight) + cost_efficiency × budget_weight

    With the default ``budget_weight=0`` the score is pure capability match:
    within a candidate pool, the most capable model wins.  Spending is controlled
    upstream by the ``max_cost_tier`` filter (which bounds the pool), not by
    penalizing capable models here.  Raise budget_weight only if you want price to
    also tip the ranking inside an already-bounded pool.

    Higher = better fit for this task.
    """
    vec = profile.capability_vector
    # Weighted dot product → capability match
    capability = sum(v * w for v, w in zip(vec, task_weights))
    capability /= max(sum(task_weights), 0.001)  # normalize to 0-1

    # Cost efficiency: cheaper = higher score (inverted and normalized)
    # Map cost to roughly 0-1 where 0.14/1M → 1.0 and 25/1M → 0.0
    cost_log = math.log2(profile.average_cost + 0.01)  # log scale
    cost_norm = 1.0 - min(1.0, (cost_log - math.log2(0.15)) / (math.log2(25) - math.log2(0.15)))
    cost_norm = max(0.0, min(1.0, cost_norm))

    return capability * (1.0 - budget_weight) + cost_norm * budget_weight


import math


# Cost-tier boundaries (USD per 1M tokens, applied to weighted average_cost).
# Override at runtime via Config.cost_tier_thresholds; see policyflow.example.yaml.
DEFAULT_COST_TIER_THRESHOLDS: dict[str, float] = {
    "cheap_max": 0.5,   # average_cost < this → cheap
    "mid_max":   1.7,   # cheap_max ≤ average_cost < this → mid
                        # ≥ mid_max → expensive
}


def select_best_model(
    specialty: str,
    available_models: list[str],
    cost_tier: str = "",
    budget_weight: float = 0.0,
    cost_tier_thresholds: dict[str, float] | None = None,
) -> str | None:
    """Pick the best model for a task type from available candidates.

    Args:
        specialty: task type key in TASK_WEIGHTS (e.g. "代码生成")
        available_models: list of model IDs that are configured and available
        cost_tier: optional budget filter ("cheap" / "mid" / "expensive") — this
            is how spending is controlled: it bounds the candidate pool.
        budget_weight: how much price tips the ranking *within* the pool
            (0=pure capability, the default; 1=only cost). Spending is normally
            governed by cost_tier, so this stays 0 unless you want price to also
            break ties among equally-capable models.
        cost_tier_thresholds: optional override for tier boundaries
            (defaults to DEFAULT_COST_TIER_THRESHOLDS)

    Returns the highest-scoring model ID, or None if no match.
    """
    weights = TASK_WEIGHTS.get(specialty)
    if not weights:
        return None

    candidates = [
        (model_id, PROFILES[model_id])
        for model_id in available_models
        if model_id in PROFILES
    ]
    if not candidates:
        return None

    # Filter by cost tier if specified
    if cost_tier in ("cheap", "mid", "expensive"):
        thresholds = cost_tier_thresholds or DEFAULT_COST_TIER_THRESHOLDS
        cheap_max = thresholds.get("cheap_max", DEFAULT_COST_TIER_THRESHOLDS["cheap_max"])
        mid_max = thresholds.get("mid_max", DEFAULT_COST_TIER_THRESHOLDS["mid_max"])
        if cost_tier == "cheap":
            candidates = [(m, p) for m, p in candidates if p.average_cost < cheap_max]
        elif cost_tier == "mid":
            candidates = [(m, p) for m, p in candidates if p.average_cost < mid_max]
        # expensive → no cap (whole pool); max_cost_tier is an upper bound,
        # so each tier includes everything cheaper than its ceiling.

    if not candidates:
        return None

    # Score and rank
    scored = [(score_model(p, weights, budget_weight), m) for m, p in candidates]
    scored.sort(reverse=True)
    return scored[0][1]


def select_best_models(
    specialty: str,
    available_models: list[str],
    n: int = 3,
    cost_tier: str = "",
    budget_weight: float = 0.0,
    cost_tier_thresholds: dict[str, float] | None = None,
) -> list[str]:
    """Return the top-N models for a task (same scoring as select_best_model).

    Used for capability model failover: if the #1 model's providers all fail,
    transparently try #2, #3, ... — no cascade switch, no pure-ability
    escalation, just "next-best by the same formula".
    """
    weights = TASK_WEIGHTS.get(specialty)
    if not weights:
        return []
    candidates = [
        (model_id, PROFILES[model_id])
        for model_id in available_models
        if model_id in PROFILES
    ]
    if not candidates:
        return []
    if cost_tier in ("cheap", "mid", "expensive"):
        thresholds = cost_tier_thresholds or DEFAULT_COST_TIER_THRESHOLDS
        cheap_max = thresholds.get("cheap_max", 0.5)
        mid_max = thresholds.get("mid_max", 1.7)
        if cost_tier == "cheap":
            candidates = [(m, p) for m, p in candidates if p.average_cost < cheap_max]
        elif cost_tier == "mid":
            candidates = [(m, p) for m, p in candidates if p.average_cost < mid_max]
        # expensive → no cap (upper-bound semantics, cumulative tiers)
    if not candidates:
        return []
    scored = [(score_model(p, weights, budget_weight), m) for m, p in candidates]
    scored.sort(reverse=True)
    return [m for _, m in scored[:n]]


def next_stronger_model(
    specialty: str,
    current_model: str,
    available_models: list[str],
) -> str | None:
    """Pick the next escalation target by pure capability (ignoring price).

    Cascade escalation happens only after a cheaper model has already failed,
    so cost is irrelevant here — we want the model that does the job best.
    Ranks available models by capability for this task (budget_weight=0) and
    returns the one ranked just ABOVE current_model — a single step up, not a
    jump to the strongest, so escalation stays gradual.

    Returns None if current_model is already the strongest, or if the task /
    models are unknown (caller should then fall back to its static chain).
    """
    weights = TASK_WEIGHTS.get(specialty)
    if not weights:
        return None
    ranked = sorted(
        (
            (score_model(PROFILES[m], weights, budget_weight=0.0), m)
            for m in available_models
            if m in PROFILES
        ),
        reverse=True,
    )  # strongest first
    models = [m for _, m in ranked]
    if current_model not in models:
        # Current model unknown/unscored — escalate to the strongest available.
        return models[0] if models else None
    idx = models.index(current_model)
    # The model just above current in capability is at idx-1 (list is desc).
    return models[idx - 1] if idx > 0 else None
