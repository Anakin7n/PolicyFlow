"""PolicyFlow configuration — loads YAML file and environment variables."""

from __future__ import annotations

import os
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


class Config:
    """PolicyFlow configuration, loaded from policyflow.yaml + env vars."""

    def __init__(self, path: str = "policyflow.yaml") -> None:
        self.path = Path(path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = yaml.safe_load(DEFAULT_CONFIG) or {}

        # Env vars override YAML values
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
        return data

    @property
    def upstream_base_url(self) -> str:
        return self.data["upstream"]["base_url"]

    @property
    def upstream_api_key(self) -> str:
        return self.data["upstream"]["api_key"]

    @property
    def upstream_timeout(self) -> int:
        return self.data["upstream"]["timeout"]
