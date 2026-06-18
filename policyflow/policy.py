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

    def __init__(self, policies_data: list[dict]) -> None:
        self.policies: list[Policy] = [Policy.from_dict(p) for p in policies_data]
        self._default: Policy | None = None

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
