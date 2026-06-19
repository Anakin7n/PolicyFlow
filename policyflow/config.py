"""PolicyFlow configuration — loads YAML file and environment variables."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CONFIG = """
upstream:
  base_url: https://api.deepseek.com
  api_key: ""
  timeout: 60
"""

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """Replace ${VAR_NAME} placeholders with environment variable values."""
    def _replace(match):
        return os.getenv(match.group(1), "")
    return _ENV_VAR_RE.sub(_replace, value)


class Config:
    """PolicyFlow configuration, loaded from policyflow.yaml + env vars."""

    def __init__(self, path: str = "policyflow.yaml") -> None:
        self.path = Path(path)
        self.data: dict = self._load()
        self._model_provider: dict[str, list[str]] = self._build_model_provider_map()
        # model_id → [provider_name, ...]  ordered by yaml appearance (first = highest priority)

    def _load(self) -> dict:
        # Try the user's config first, then the example template
        candidates = [self.path, Path("policyflow.example.yaml")]
        for p in candidates:
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    if isinstance(data, dict):
                        break
        else:
            data = yaml.safe_load(DEFAULT_CONFIG) or {}

        if not isinstance(data, dict):
            raise ValueError(
                f"policyflow.yaml must be a mapping, got {type(data).__name__}. "
                "Check for top-level YAML sequence or scalar."
            )

        # ── Default upstream (env var overrides) ─────────────────────
        data.setdefault("upstream", {})
        data["upstream"]["base_url"] = os.getenv(
            "UPSTREAM_BASE_URL", data["upstream"].get("base_url", "http://localhost:3000")
        )
        data["upstream"]["api_key"] = os.getenv(
            "UPSTREAM_API_KEY", data["upstream"].get("api_key", "")
        )
        data["upstream"]["timeout"] = int(
            os.getenv("UPSTREAM_TIMEOUT", data["upstream"].get("timeout", 60))
        )

        # ── Resolve ${VAR} placeholders in api_keys ────────────────────
        # Embedding
        if "embedding" in data and "api_key" in data["embedding"]:
            data["embedding"]["api_key"] = _resolve_env(data["embedding"]["api_key"])
        # Providers
        providers_data = data.get("providers", {})
        if isinstance(providers_data, dict):
            for cfg in providers_data.values():
                if "api_key" in cfg:
                    cfg["api_key"] = _resolve_env(cfg["api_key"])

        return data

    def _build_model_provider_map(self) -> dict[str, list[str]]:
        """Build a reverse index: model_id → [provider_name, ...].

        Provider order is yaml appearance — first listed provider for a model
        is tried first. If a provider fails with a transient error (quota
        exhausted, rate-limited, server down), the next provider in this
        list is tried automatically.

        providers is a dict like:
            { "deepseek": { base_url: ..., models: [...] }, "anthropic": {...} }
        """
        model_map: dict[str, list[str]] = {}
        providers = self.data.get("providers", {})
        if isinstance(providers, dict):
            for name, cfg in providers.items():
                for model in cfg.get("models", []):
                    if model not in model_map:
                        model_map[model] = []
                    model_map[model].append(name)
        return model_map

    def get_model_provider(self, model_id: str) -> str | None:
        """Return the primary (first-listed) provider for a model, or None."""
        providers = self.get_model_providers(model_id)
        return providers[0] if providers else None

    def get_model_providers(self, model_id: str) -> list[str]:
        """Return all providers for a model, in yaml-appearance order.

        The first provider is tried first; on transient failure, the next
        provider in this list is used automatically (see UpstreamProxy).
        """
        return self._model_provider.get(model_id, [])

    def get_provider_config(self, provider_name: str) -> dict:
        """Return {base_url, api_key, timeout} for a provider."""
        providers = self.data.get("providers", {})
        if isinstance(providers, dict):
            cfg = providers.get(provider_name, {})
            return {
                "base_url": cfg.get("base_url", self.upstream_base_url),
                "api_key": cfg.get("api_key", self.upstream_api_key),
                "timeout": cfg.get("timeout", self.upstream_timeout),
            }
        # Fallback to default upstream
        return {
            "base_url": self.upstream_base_url,
            "api_key": self.upstream_api_key,
            "timeout": self.upstream_timeout,
        }

    @property
    def upstream_base_url(self) -> str:
        return self.data["upstream"]["base_url"]

    @property
    def upstream_api_key(self) -> str:
        return self.data["upstream"]["api_key"]

    @property
    def upstream_timeout(self) -> int:
        return self.data["upstream"]["timeout"]

    # ── Embedding ─────────────────────────────────────────────────

    @property
    def embedding_base_url(self) -> str:
        url = self.data.get("embedding", {}).get("base_url", "")
        return url or self.upstream_base_url

    @property
    def embedding_api_key(self) -> str:
        key = self.data.get("embedding", {}).get("api_key", "")
        return key or self.upstream_api_key

    @property
    def embedding_model(self) -> str:
        return self.data.get("embedding", {}).get("model", "text-embedding-3-small")

    @property
    def embedding_threshold(self) -> float:
        return float(self.data.get("embedding", {}).get("similarity_threshold", 0.5))

    @property
    def embedding_verify_threshold(self) -> float:
        """Threshold for keyword-match verification — looser than the main threshold.

        After a keyword match, the prompt is re-embedded against the matched
        policy. If similarity drops below this value, the keyword hit is
        treated as a false positive (e.g. "苹果手机" hitting a fruit policy)
        and routing falls through to embedding global match.
        """
        return float(self.data.get("embedding", {}).get("verify_threshold", 0.5))

    @property
    def embedding_timeout(self) -> int:
        return int(self.data.get("embedding", {}).get("timeout", 30))

    @property
    def cost_tier_thresholds(self) -> dict[str, float]:
        """USD/M-token boundaries for `max_cost_tier: cheap|mid|expensive`.

        Defaults to {cheap_max: 1.0, mid_max: 5.0}. Tiers are computed against
        the weighted average_cost (3:1 input:output), so e.g. claude-haiku
        (input 1.0 / output 5.0 → avg 2.0) lands in mid by default.
        """
        cfg = self.data.get("cost_tiers", {})
        return {
            "cheap_max": float(cfg.get("cheap_max", 1.0)),
            "mid_max": float(cfg.get("mid_max", 5.0)),
        }

    # ── Policies ──────────────────────────────────────────────────

    @property
    def routing_mode(self) -> str:
        """Global routing mode: 'explicit' | 'capability' | 'hybrid' (default).

        - explicit:    All policies use route_to, ignore specialty.
        - capability:  All policies use specialty, auto-detect if missing.
        - hybrid:      Per-policy choice (has specialty → capability; else → route_to).

        Override via env: POLICYFLOW_ROUTING_MODE
        """
        env = os.getenv("POLICYFLOW_ROUTING_MODE", "")
        return env or self.data.get("routing_mode", "hybrid")

    @property
    def policies_data(self) -> list[dict]:
        """Return the active policy set based on routing_mode.

        Mode-specific sets: policies_hybrid / policies_capability / policies_explicit.
        Falls back to ``policies`` for backward compatibility if the mode key is missing.
        """
        key = f"policies_{self.routing_mode}"
        if key in self.data:
            return self.data[key]
        # Fallback for old configs that only have "policies"
        return self.data.get("policies", [])

    # ── Cascade ────────────────────────────────────────────────────

    @property
    def cascade_data(self) -> dict:
        return self.data.get("cascade", {})

    # ── Logging ───────────────────────────────────────────────────

    @property
    def log_prompt_preview(self) -> bool:
        return bool(self.data.get("logging", {}).get("log_prompt_preview", False))

    # ── Optimizer ─────────────────────────────────────────────────

    @property
    def optimizer_data(self) -> dict:
        return self.data.get("optimizer", {})

    # ── Modifiers ──────────────────────────────────────────────────

    @property
    def modifiers_data(self) -> dict:
        return self.data.get("modifiers", {})
