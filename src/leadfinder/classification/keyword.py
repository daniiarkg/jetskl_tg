from __future__ import annotations

import re

from leadfinder.classification.types import MessageContext
from leadfinder.profiles import SearchProfileSpec


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_term(text: str, term: str) -> bool:
    normalized = _normalize(term)
    if not normalized:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text) is not None


class KeywordScorer:
    def score(
        self,
        profile: SearchProfileSpec,
        context: MessageContext,
    ) -> tuple[float, tuple[str, ...]]:
        text = _normalize(context.text)
        reasons: list[str] = []

        service_hits = [item for item in profile.services if _contains_term(text, item)]
        location_hits = [item for item in profile.locations if _contains_term(text, item)]
        intent_hits = [item for item in profile.intents if _contains_term(text, item)]
        negative_hits = [item for item in profile.negative_terms if _contains_term(text, item)]

        score = 0.0
        if service_hits:
            score += 0.38
            reasons.append(f"service:{service_hits[0]}")
        if location_hits:
            score += 0.27
            reasons.append(f"location:{location_hits[0]}")
        if intent_hits:
            score += 0.25
            reasons.append(f"intent:{intent_hits[0]}")

        question_signals = (
            "?",
            "where can",
            "any recommendation",
            "looking for",
            "need to",
            "does anyone",
            "alguien",
            "recomiendan",
            "где",
            "кто знает",
            "посоветуйте",
        )
        if (service_hits or location_hits or intent_hits) and any(
            signal in text for signal in question_signals
        ):
            score += 0.10
            reasons.append("buyer-question")

        if negative_hits:
            score -= 0.65
            reasons.append(f"negative:{negative_hits[0]}")

        # Keyword-only qualification must contain the requested service. Semantic
        # candidates without an exact service term can still be rescued by embeddings.
        if not service_hits:
            score = min(score, 0.20)
            reasons.append("missing-service")

        return max(0.0, min(1.0, score)), tuple(reasons)
