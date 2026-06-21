"""Policy data model and YAML parsing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Policy:
    """A single routing policy — maps request characteristics to a target model."""

    name: str
    route_to: str = ""              # explicit model (legacy path)
    keywords: list[str] = field(default_factory=list)
    description: list[str] = field(default_factory=list)  # natural-language descriptions for centroid embedding
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
            description=data.get("description", []),
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
        """All keywords joined into a single string (legacy fallback for embedding)."""
        return " ".join(self.keywords)

class PolicyEngine:
    """Loads and holds policies from YAML config."""

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
