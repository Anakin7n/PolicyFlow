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

    @property
    def uses_capability_routing(self) -> bool:
        """Whether this policy should use capability-aware model selection."""
        return bool(self.specialty) and not self.route_to


class PolicyEngine:
    """Loads and holds policies from YAML config."""

    # ── Auto-detection: keyword → specialty ──────────────────────
    _SPECIALTY_HINTS: dict[str, list[str]] = {
        "代码生成":     ["代码", "编程", "写代码", "debug", "重构", "单元测试", "算法实现", "API接口", "SQL查询"],
        "代码审查":     ["审查", "review", "修bug", "代码审查"],
        "数学推理":     ["数学", "证明", "计算", "公式"],
        "逻辑分析":     ["推理", "分析", "逻辑", "策略"],
        "文本创作":     ["写作", "创作", "文章", "文案", "扩写"],
        "翻译校对":     ["翻译", "摘要", "改写", "润色", "纠错", "格式化"],
        "Agent工具调用": ["agent", "工具", "插件", "函数调用"],
        "系统设计":     ["架构", "系统设计", "技术方案", "微服务"],
        "安全审计":     ["安全", "审计", "漏洞"],
        "性能优化":     ["性能", "优化", "加速"],
        "图片理解":     ["图片", "图像", "照片", "截图"],
    }

    @classmethod
    def _infer_specialty(cls, policy: Policy) -> str:
        """Guess the best specialty from a policy's name + keywords."""
        text = policy.name + " " + " ".join(policy.keywords)
        for specialty, hints in cls._SPECIALTY_HINTS.items():
            if any(h in text for h in hints):
                return specialty
        return "通用问答"

    def __init__(self, policies_data: list[dict], routing_mode: str = "hybrid") -> None:
        self.policies: list[Policy] = [Policy.from_dict(p) for p in policies_data]
        self.routing_mode = routing_mode
        self._default: Policy | None = None

    def uses_capability_routing(self, policy: Policy) -> bool:
        """Whether this policy should use capability-aware model selection.

        Governed by global ``routing_mode``:
        - ``"explicit"``   → never; the policy set is already all-route_to
        - ``"capability"`` → always (auto-detect specialty if missing)
        - ``"hybrid"``     → per-policy: has specialty → capability
        """
        if self.routing_mode == "explicit":
            return False
        if self.routing_mode == "capability":
            if not policy.specialty:
                policy.specialty = self._infer_specialty(policy)
            return True
        # hybrid: per-policy
        return bool(policy.specialty) and not policy.route_to

    @property
    def default(self) -> Policy | None:
        if self._default is None:
            for p in self.policies:
                if p.default:
                    self._default = p
                    break
        return self._default

    @property
    def non_default_policies(self) -> list[Policy]:
        return [p for p in self.policies if not p.default]
