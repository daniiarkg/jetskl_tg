from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from leadfinder.profiles import SearchProfileSpec


@dataclass(frozen=True, slots=True)
class MessageContext:
    text: str
    query: str
    language: str | None = None
    source_language: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    is_candidate: bool
    final_score: float
    keyword_score: float
    embedding_score: float | None = None
    llm_score: float | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    extracted_data: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LLMDecision:
    score: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    extracted_data: dict[str, object] = field(default_factory=dict)


class EmbeddingBackend(Protocol):
    def similarity(self, query: str, text: str) -> float: ...


class LLMBackend(Protocol):
    def classify(
        self,
        profile: SearchProfileSpec,
        context: MessageContext,
    ) -> LLMDecision: ...
