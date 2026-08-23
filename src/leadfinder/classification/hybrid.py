from __future__ import annotations

from leadfinder.classification.keyword import KeywordScorer
from leadfinder.classification.types import (
    ClassificationResult,
    EmbeddingBackend,
    LLMBackend,
    MessageContext,
)
from leadfinder.languages import (
    language_filter_description,
    language_is_allowed,
    normalize_language_code,
)
from leadfinder.profiles import SearchProfileSpec


class HybridClassifier:
    def __init__(
        self,
        embedding_backend: EmbeddingBackend | None = None,
        llm_backend: LLMBackend | None = None,
        candidate_threshold: float = 0.55,
        require_llm_confirmation: bool = False,
    ):
        self.keyword = KeywordScorer()
        self.embedding_backend = embedding_backend
        self.llm_backend = llm_backend
        self.candidate_threshold = candidate_threshold
        self.require_llm_confirmation = require_llm_confirmation

    def classify(
        self,
        profile: SearchProfileSpec,
        context: MessageContext,
    ) -> ClassificationResult:
        keyword_score, keyword_reasons = self.keyword.score(profile, context)
        embedding_score = None
        llm_score = None
        reasons = list(keyword_reasons)
        extracted_data: dict[str, object] = {}

        has_service_signal = any(
            reason.startswith("service:") for reason in keyword_reasons
        )
        if self.require_llm_confirmation and not has_service_signal:
            reasons.append("prefilter:no-service-signal")
            return ClassificationResult(
                is_candidate=False,
                final_score=0.0,
                keyword_score=keyword_score,
                reasons=tuple(reasons),
            )

        weighted_total = keyword_score * 0.55
        weight_total = 0.55

        if self.embedding_backend is not None:
            embedding_score = self.embedding_backend.similarity(
                f"{profile.classifier_prompt} Allowed languages: "
                f"{language_filter_description(profile.languages)}",
                context.text,
            )
            weighted_total += embedding_score * 0.30
            weight_total += 0.30
            reasons.append(f"embedding:{embedding_score:.2f}")

        # LLM is intentionally the last stage. Very weak candidates never reach it.
        if self.llm_backend is not None and (
            keyword_score >= 0.15 or (embedding_score or 0.0) >= 0.40
        ):
            decision = self.llm_backend.classify(profile, context)
            llm_score = decision.score
            reasons.extend(decision.reasons)
            extracted_data.update(decision.extracted_data)
            detected_language = normalize_language_code(
                str(extracted_data.get("language") or context.language or "")
            )
            source_language = normalize_language_code(context.source_language)
            if detected_language:
                extracted_data["language"] = detected_language
            if source_language:
                extracted_data["source_language"] = source_language
            message_allowed = language_is_allowed(profile.languages, detected_language)
            source_allowed = bool(source_language) and language_is_allowed(
                profile.languages, source_language
            )
            if not message_allowed and not source_allowed:
                llm_score = min(llm_score, 0.05)
                reasons.append(f"language:not-allowed:{detected_language}")
            elif not message_allowed and source_allowed:
                reasons.append(f"language:allowed-by-source:{source_language}")

        if self.require_llm_confirmation:
            if embedding_score is None:
                raise RuntimeError("Required embedding classification was not executed")
            if llm_score is None:
                final_score = min(0.49, keyword_score * 0.35 + embedding_score * 0.45)
            else:
                final_score = (
                    keyword_score * 0.15 + embedding_score * 0.25 + llm_score * 0.60
                )
            is_candidate = (
                llm_score is not None
                and llm_score >= 0.55
                and final_score >= self.candidate_threshold
            )
        else:
            if llm_score is not None:
                weighted_total += llm_score * 0.15
                weight_total += 0.15
            final_score = weighted_total / weight_total
            is_candidate = final_score >= self.candidate_threshold
        final_score = max(0.0, min(1.0, final_score))
        return ClassificationResult(
            is_candidate=is_candidate,
            final_score=final_score,
            keyword_score=keyword_score,
            embedding_score=embedding_score,
            llm_score=llm_score,
            reasons=tuple(reasons),
            extracted_data=extracted_data,
        )
