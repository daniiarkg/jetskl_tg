from __future__ import annotations

from leadfinder.classification.gemini_backend import (
    GeminiEmbeddingBackend,
    GeminiLLMBackend,
)
from leadfinder.classification.hybrid import HybridClassifier
from leadfinder.config import Settings


def build_classifier(settings: Settings) -> HybridClassifier:
    api_key = settings.require_gemini_api_key()
    embedding = GeminiEmbeddingBackend(
        api_key=api_key,
        model=settings.gemini_embedding_model,
        output_dimensionality=settings.gemini_embedding_dimensions,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    llm = GeminiLLMBackend(
        api_key=api_key,
        model=settings.gemini_llm_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )
    return HybridClassifier(
        embedding_backend=embedding,
        llm_backend=llm,
        candidate_threshold=settings.lead_candidate_threshold,
        require_llm_confirmation=True,
    )
