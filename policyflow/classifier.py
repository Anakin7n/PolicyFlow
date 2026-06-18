"""Embedding classifier — calls embedding API + cosine similarity matching."""

from __future__ import annotations

import numpy as np
import httpx


class EmbeddingClassifier:
    """Embeds prompt text and matches against pre-computed policy keyword embeddings."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-3-small",
        threshold: float = 0.75,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.threshold = threshold
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # Pre-computed policy embeddings: {policy_name: ndarray}
        self.policy_embeddings: dict[str, np.ndarray] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Call the embedding API, return embeddings for each text."""
        client = await self._get_client()
        response = await client.post(
            "/embeddings",
            json={"input": texts, "model": self.model},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Embedding API error {response.status_code}: {response.text[:500]}")
        data = response.json()
        # Sort by index to preserve order
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    async def embed_prompt(self, text: str) -> np.ndarray:
        """Embed a single prompt text, return as numpy array."""
        embeddings = await self._embed([text])
        return np.array(embeddings[0])

    async def init_policies(self, policies: list) -> None:
        """Pre-compute keyword embeddings for all non-default policies at startup."""
        texts = []
        names = []
        for p in policies:
            if p.keyword_text and not p.default:
                texts.append(p.keyword_text)
                names.append(p.name)

        if not texts:
            return

        embeddings = await self._embed(texts)
        for name, emb in zip(names, embeddings):
            self.policy_embeddings[name] = np.array(emb)

    def match(self, prompt_embedding: np.ndarray) -> tuple[str | None, float]:
        """Find the best-matching policy by cosine similarity.

        Returns (policy_name, similarity_score) or (None, 0.0) if no match.
        """
        best_name = None
        best_score = 0.0

        for name, policy_emb in self.policy_embeddings.items():
            score = self._cosine_similarity(prompt_embedding, policy_emb)
            if score > best_score:
                best_score = score
                best_name = name

        if best_score >= self.threshold:
            return best_name, best_score
        return None, best_score

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)
