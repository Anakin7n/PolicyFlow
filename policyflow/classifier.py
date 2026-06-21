"""Embedding classifier — calls embedding API + cosine similarity matching."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import httpx

logger = logging.getLogger(__name__)

# Persistent cache for policy embeddings — keyed by (model + description) hash
# so swapping embedding models or editing descriptions naturally invalidates.
# Lives in the project root so it's easy to inspect/clear.
_CACHE_PATH = Path(__file__).resolve().parent.parent / ".policyflow_cache" / "embeddings.json"


def _cache_load() -> dict[str, list[float]]:
    """Read the on-disk cache, return {} on any error (cache is best-effort)."""
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Embedding cache unreadable, ignoring: %s", exc)
        return {}


def _cache_save(cache: dict[str, list[float]]) -> None:
    """Persist the cache; failure is non-fatal."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist embedding cache: %s", exc)


def _cache_key(model: str, texts: list[str]) -> str:
    """Stable key over (embedding model, description list) — order matters."""
    blob = model + "\n" + "\n".join(texts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class EmbeddingClassifier:
    """Embeds prompt text and matches against pre-computed policy centroid embeddings.

    Each policy is represented by the *centroid* (mean) of embeddings of its
    description sentences — a multi-anchor approach that captures the policy's
    semantic region better than embedding a keyword bag.  Policies without a
    description fall back to embedding their keyword text (legacy behavior).
    """

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
        # 自动检测：豆包多模态 Embedding 用不同端点和输入格式
        self._multimodal = model.startswith("doubao-embedding-vision")
        self._client: httpx.AsyncClient | None = None
        # Pre-computed policy embeddings: {policy_name: ndarray}
        self.policy_embeddings: dict[str, np.ndarray] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
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
        """Call the embedding API, return embeddings for each text.

        Supports two formats:
        - openai: {"input": ["text1", "text2"], "model": "..."}  → POST /embeddings
          Response: {"data": [{"index":0,"embedding":[...]}, ...]}
        - doubao_multimodal: {"input": [{"type":"text","text":"..."}], ...} → POST /embeddings/multimodal
          Response: {"data": {"embedding": [...]}} — one embedding per request
        """
        client = await self._get_client()
        if self._multimodal:
            # Multimodal API returns one embedding per request — call individually
            embeddings: list[list[float]] = []
            for t in texts:
                response = await client.post(
                    "/embeddings/multimodal",
                    json={
                        "model": self.model,
                        "input": [{"type": "text", "text": t}],
                    },
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Embedding API error {response.status_code}: {response.text[:500]}"
                    )
                embeddings.append(response.json()["data"]["embedding"])
            return embeddings
        else:
            # OpenAI-compatible: batch all texts in one request
            response = await client.post(
                "/embeddings",
                json={"input": texts, "model": self.model},
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Embedding API error {response.status_code}: {response.text[:500]}"
                )
            data = response.json()
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]

    async def embed_prompt(self, text: str) -> np.ndarray:
        """Embed a single prompt text, return as numpy array."""
        embeddings = await self._embed([text])
        return np.array(embeddings[0])

    async def init_policies(self, policies: list) -> None:
        """Pre-compute policy centroid embeddings at startup.

        For each policy, embed every description sentence and average the
        resulting vectors → that average is the policy's centroid.  Policies
        without a description fall back to embedding their keyword text as a
        single sentence (legacy behavior, less precise but still works).

        Embeddings are cached on disk keyed by (model + description) hash —
        cold start computes ~60-100 vectors (slow), warm start reads from
        disk (instant).
        """
        cache = _cache_load()
        cache_dirty = False

        for p in policies:
            if p.default:
                continue

            # Description list is preferred; keyword_text is the legacy fallback.
            texts = p.description or ([p.keyword_text] if p.keyword_text else [])
            if not texts:
                continue

            key = _cache_key(self.model, texts)
            cached = cache.get(key)
            if cached is not None:
                self.policy_embeddings[p.name] = np.array(cached)
                continue

            # Cache miss → embed each text, average to a centroid.
            embeddings = await self._embed(texts)
            centroid = np.mean(np.array(embeddings), axis=0)
            self.policy_embeddings[p.name] = centroid
            cache[key] = centroid.tolist()
            cache_dirty = True
            logger.info("Computed centroid for policy %r (%d anchors)", p.name, len(texts))

        if cache_dirty:
            _cache_save(cache)

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

    def similarity_to(self, prompt_embedding: np.ndarray, policy_name: str) -> float:
        """Cosine similarity between a prompt and a single specific policy.

        Used for keyword-match verification: when the keyword stage hits a policy,
        we re-check that the prompt is *semantically* close to the policy's keyword
        embedding, to avoid false hits like "苹果手机" matching a "fruit" policy
        whose keywords contain the word "苹果".
        """
        policy_emb = self.policy_embeddings.get(policy_name)
        if policy_emb is None:
            return 1.0  # No embedding for this policy → trust the keyword match
        return self._cosine_similarity(prompt_embedding, policy_emb)

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)
