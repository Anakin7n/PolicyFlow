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
  base_url: http://localhost:3000
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
        self._model_provider: dict[str, str] = self._build_model_provider_map()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = yaml.safe_load(DEFAULT_CONFIG) or {}

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

        # ── Resolve env vars in provider api_keys ────────────────────
        providers_data = data.get("providers", {})
        if isinstance(providers_data, dict):
            for cfg in providers_data.values():
                if "api_key" in cfg:
                    cfg["api_key"] = _resolve_env(cfg["api_key"])

        return data

    def _build_model_provider_map(self) -> dict[str, str]:
        """Build a reverse index: model_id → provider_name.

        providers is a dict like:
            { "one-api": { base_url: ..., models: [...] }, "deepseek": {...} }
        """
        model_map: dict[str, str] = {}
        providers = self.data.get("providers", {})
        if isinstance(providers, dict):
            for name, cfg in providers.items():
                for model in cfg.get("models", []):
                    model_map[model] = name
        return model_map

    def get_model_provider(self, model_id: str) -> str | None:
        """Return the provider name for a model, or None if using default upstream."""
        return self._model_provider.get(model_id)

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
        return float(self.data.get("embedding", {}).get("similarity_threshold", 0.75))

    @property
    def embedding_timeout(self) -> int:
        return int(self.data.get("embedding", {}).get("timeout", 30))

    # ── Policies ──────────────────────────────────────────────────

    @property
    def policies_data(self) -> list[dict]:
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
