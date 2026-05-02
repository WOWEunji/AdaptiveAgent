"""Embedding-based skill search backends (issue #17).

Optional opt-in feature. Default is ``NoopEmbedder`` which makes the catalog
behave exactly like before (keyword-only search). Local backend uses
``sentence-transformers`` (heavy dep, requires ``pip install adaptive-agent[embeddings]``).
OpenAI backend uses the existing ``openai`` dependency.

Decision (issue #17): both opt-in via ``AgentConfig.embedding_provider``,
default ``"none"``. Cosine similarity threshold default 0.4. Embeddings are
stored on manifest entries (``embedding`` + ``embedding_model``) so a model
change triggers lazy recomputation.
"""

from __future__ import annotations

import math
from typing import Protocol


class Embedder(Protocol):
    """Minimum protocol for an embedding provider."""

    model_id: str

    def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector or ``None`` if backend unavailable."""


class NoopEmbedder:
    """Default backend — returns None so callers fall back to keyword search."""

    model_id = "noop"

    def embed(self, text: str) -> list[float] | None:
        return None


class LocalEmbedder:
    """Local sentence-transformers embedder (heavy optional dep).

    Lazy import: import error at first call, not at module import. Allows
    the package to install without sentence-transformers and degrade
    gracefully.
    """

    def __init__(self, model_id: str = "paraphrase-multilingual-MiniLM-L12-v2") -> None:
        self.model_id = model_id
        self._model: object | None = None

    def _ensure_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "LocalEmbedder는 'sentence-transformers' 패키지가 필요합니다. "
                "`pip install adaptive-agent[embeddings]` 또는 "
                "`pip install sentence-transformers`를 실행하거나, "
                "AgentConfig.embedding_provider를 'none' 또는 'openai'로 설정하세요."
            ) from exc
        self._model = SentenceTransformer(self.model_id)
        return self._model

    def embed(self, text: str) -> list[float] | None:
        if not text.strip():
            return None
        model = self._ensure_model()
        vec = model.encode([text], normalize_embeddings=True)[0]  # type: ignore[attr-defined]
        return [float(x) for x in vec]


class OpenAIEmbedder:
    """OpenAI embedding API backend (uses existing ``openai`` dep)."""

    def __init__(self, model_id: str = "text-embedding-3-small", *, api_key: str | None = None) -> None:
        self.model_id = model_id
        self._api_key = api_key

    def embed(self, text: str) -> list[float] | None:
        if not text.strip():
            return None
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIEmbedder는 'openai' 패키지가 필요합니다. requirements.txt 또는 "
                "pyproject.toml의 기본 의존성에 포함되어 있어야 합니다."
            ) from exc
        import os

        api_key = self._api_key or os.getenv("OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
        response = client.embeddings.create(model=self.model_id, input=text)
        return [float(x) for x in response.data[0].embedding]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [-1, 1]; 0 for length mismatch or zero norm."""

    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def create_embedder(provider: str, *, model_id: str | None = None) -> Embedder:
    """Factory: provider ∈ {'none', 'local', 'openai'}."""

    p = provider.lower().strip()
    if p == "none" or p == "":
        return NoopEmbedder()
    if p == "local":
        return LocalEmbedder(model_id=model_id or "paraphrase-multilingual-MiniLM-L12-v2")
    if p == "openai":
        return OpenAIEmbedder(model_id=model_id or "text-embedding-3-small")
    raise ValueError(f"지원하지 않는 embedding_provider: {provider!r} (none/local/openai)")
