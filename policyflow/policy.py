"""Policy data model and YAML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Policy:
    """A single routing policy — maps request characteristics to a target model."""

    name: str
    route_to: str = ""              # explicit model (legacy path)
    keywords: list[str] = field(default_factory=list)
    max_input_tokens: int | None = None
    min_input_tokens: int | None = None
    has_image: bool = False
    default: bool = False
    cascade: bool = False
    specialty: str = ""              # capability-aware auto-select (新)
    max_cost_tier: str = ""          # budget constraint: cheap|mid|expensive

    @classmethod
    def from_dict(cls, data: dict) -> Policy:
        match = data.get("match", {})
        return cls(
            name=data["name"],
            route_to=data.get("route_to", ""),
            keywords=match.get("keywords", []),
            max_input_tokens=match.get("max_input_tokens"),
            min_input_tokens=match.get("min_input_tokens"),
            has_image=match.get("has_image", False),
            default=match.get("default", False),
            cascade=data.get("cascade", False),
            specialty=data.get("specialty", ""),
            max_cost_tier=data.get("max_cost_tier", ""),
        )

    @property
    def keyword_text(self) -> str:
        """All keywords joined into a single string for embedding."""
        return " ".join(self.keywords)

class PolicyEngine:
    """Loads and holds policies from YAML config."""

    # ── Auto-detection: keyword → specialty ──────────────────────
    _SPECIALTY_HINTS: dict[str, list[str]] = {
        "图片理解":     ["图片", "图像", "照片", "截图"],
        "代码生成":     ["代码", "编程", "写代码", "debug", "重构", "单元测试", "算法实现", "API接口", "SQL查询"],
        "代码审查":     ["审查", "review", "修bug", "代码审查"],
        "数据分析":     ["分析数据", "Excel", "CSV", "表格", "统计"],
        "文本创作":     ["写作", "创作", "文章", "文案", "扩写", "写诗", "故事"],
        "翻译校对":     ["翻译", "摘要", "改写", "润色", "纠错", "格式化"],
        "复杂推理":     ["推理", "逻辑", "策略", "数学证明", "推导"],
        "系统架构":     ["架构", "高并发", "分布式", "微服务"],
        "安全审计":     ["安全审计", "威胁建模", "漏洞", "渗透", "加密"],
        "性能分析":     ["性能", "优化", "调优", "瓶颈", "加速"],
        "知识问答":     ["为什么", "原理", "起源", "科普", "入门"],
        "日常闲聊":     ["你好", "hi", "在吗", "谢谢", "晚安"],
        "默认":         [],
    }

    @classmethod
    def _infer_specialty(cls, policy: Policy) -> str:
        """Map a policy to its task type, using the policy name as the primary key.

        The policy name IS the specialty — "代码生成" → "代码生成",
        "代码审查与调试" → "代码审查" (fuzzy match).  This means strategy
        naming and model selection use the same vocabulary; no translation
        step needed.
        """
        from .model_profiles import TASK_WEIGHTS

        # 1. Exact match: the policy name is a TASK_WEIGHTS key
        if policy.name in TASK_WEIGHTS:
            return policy.name

        # 2. Substring match: "代码审查与调试" contains "代码审查"
        for key in TASK_WEIGHTS:
            if key in policy.name:
                return key

        # 3. Keyword-based fallback (should rarely run with a well-named policy)
        for key in TASK_WEIGHTS:
            for hint in cls._SPECIALTY_HINTS.get(key, []):
                if hint in policy.name or any(hint in kw for kw in policy.keywords):
                    return key

        return "默认"

    def __init__(self, policies_data: list[dict], routing_mode: str = "hybrid") -> None:
        self.policies: list[Policy] = [Policy.from_dict(p) for p in policies_data]
        self.routing_mode = routing_mode

    def uses_capability_routing(self, policy: Policy) -> bool:
        """Whether this policy should use capability-aware model selection.

        - ``explicit``   → never
        - ``capability`` → always (auto-detect task type from policy name)
        - ``hybrid``     → only when route_to is empty
        """
        if self.routing_mode == "explicit":
            return False
        if self.routing_mode == "capability":
            return True
        # hybrid: no route_to → let the system pick
        return not bool(policy.route_to)

    @property
    def non_default_policies(self) -> list[Policy]:
        return [p for p in self.policies if not p.default]
