from __future__ import annotations

from typing import Any


class SentenceTransformerEmbeddingBackend:
    """Lazy local embedding backend; no Telegram text leaves the machine."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Any | None = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Local embeddings are enabled but the AI extra is not installed. "
                "Run: uv sync --extra embeddings"
            ) from exc
        self._model = SentenceTransformer(self.model_name)
        return self._model

    def similarity(self, query: str, text: str) -> float:
        model = self._load_model()
        vectors = model.encode(
            [query, text],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        cosine = float(vectors[0] @ vectors[1])
        return max(0.0, min(1.0, cosine))
