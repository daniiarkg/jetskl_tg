from dataclasses import replace

from leadfinder.classification import HybridClassifier, MessageContext
from leadfinder.classification.schemas import LeadDecisionPayload, lead_probability
from leadfinder.classification.types import LLMDecision
from leadfinder.profiles import JETSKI_MIAMI


class FakeEmbeddingBackend:
    def __init__(self, score: float):
        self.score = score

    def similarity(self, query: str, text: str) -> float:
        return self.score


class FakeLLMBackend:
    def __init__(self, score: float, language: str | None = None):
        self.score = score
        self.language = language

    def classify(self, _profile, _context) -> LLMDecision:
        extracted_data = {"language": self.language} if self.language else {}
        return LLMDecision(
            score=self.score,
            reasons=("gemini:test",),
            extracted_data=extracted_data,
        )


def test_negative_llm_boolean_cannot_become_high_score() -> None:
    payload = LeadDecisionPayload(
        is_potential_customer=False,
        confidence=0.83,
        is_provider_ad=False,
        reason="Unrelated parcel delivery offer",
        language="ru",
    )

    assert lead_probability(payload) == 0.05


def test_structured_payload_preserves_explicit_iso_event_date() -> None:
    payload = LeadDecisionPayload(
        is_potential_customer=True,
        confidence=0.9,
        is_provider_ad=False,
        reason="Buyer asks for a future date",
        event_date_text="10 September 2026",
        event_date="2026-09-10",
    )

    assert payload.event_date == "2026-09-10"


def test_keyword_classifier_accepts_buyer_intent() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Where can I rent two jet skis in Miami tomorrow?",
            query="jet ski rental Miami",
        ),
    )

    assert result.is_candidate
    assert result.keyword_score >= 0.9


def test_keyword_classifier_accepts_researched_mid_beach_buyer_message() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="где выгодно взять jetski mid-beach miami",
            query="Русскоязычный чат Майами",
            source_language="ru",
        ),
    )

    assert result.is_candidate
    assert result.keyword_score == 1.0
    assert "service:jetski" in result.reasons
    assert "location:Miami" in result.reasons
    assert "intent:где выгодно взять" in result.reasons


def test_keyword_classifier_rejects_provider_ad() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="We offer jet ski rental Miami. DM for booking and best prices.",
            query="jet ski rental Miami",
        ),
    )

    assert not result.is_candidate
    assert result.keyword_score < 0.5


def test_keyword_classifier_accepts_colloquial_typo_in_miami_area() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Ребята, кто сдаёт два jetsky в Санни-Айлс на завтра?",
            query="Русскоязычный чат Майами",
            source_language="ru",
        ),
    )

    assert result.is_candidate
    assert result.keyword_score == 1.0
    assert "service:jetsky" in result.reasons
    assert "location:Санни-Айлс" in result.reasons


def test_keyword_classifier_accepts_aquabike_island_request() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="У кого есть контакт, чтобы взять аквабайк до острова енотов?",
            query="Русскоязычный чат Майами",
            source_language="ru",
        ),
    )

    assert result.is_candidate
    assert "service:аквабайк" in result.reasons
    assert "location:острова енотов" in result.reasons


def test_keyword_classifier_rejects_russian_provider_promo() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text=(
                "Мы сдаём гидроциклы в Sunny Isles. Наш парк, "
                "лучшие цены и промокод MIAMI10. Пишите в директ."
            ),
            query="jet ski Miami",
            source_language="ru",
        ),
    )

    assert not result.is_candidate
    assert result.keyword_score < 0.5
    assert any(reason.startswith("negative:") for reason in result.reasons)


def test_keyword_classifier_rejects_past_experience_without_new_demand() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Вчера покатались на гидроциклах в Key Biscayne, было классно.",
            query="jet ski Miami",
            source_language="ru",
        ),
    )

    assert not result.is_candidate
    assert "negative:покатались" in result.reasons


def test_embedding_contributes_to_hybrid_score() -> None:
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.9),
        candidate_threshold=0.5,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Any fun water activities around South Beach for tomorrow?",
            query="Miami water activities",
        ),
    )

    assert result.embedding_score == 0.9
    assert result.final_score > result.keyword_score * 0.55


def test_intent_terms_do_not_match_inside_unrelated_words() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Current Facebook marketing opportunities in Miami",
            query="Miami community",
        ),
    )

    assert not result.is_candidate
    assert result.keyword_score <= 0.2
    assert not any(reason.startswith("intent:") for reason in result.reasons)


def test_keyword_only_candidate_requires_jetski_service() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Where can I rent an apartment in Miami?",
            query="Miami community",
        ),
    )

    assert not result.is_candidate
    assert "missing-service" in result.reasons


def test_required_llm_is_final_authority() -> None:
    buyer = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.9),
        llm_backend=FakeLLMBackend(0.98),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Need two wave runners around South Beach tomorrow",
            query="jet ski rental Miami",
        ),
    )
    provider = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.95),
        llm_backend=FakeLLMBackend(0.02),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Jet ski rentals available in Miami, book now",
            query="jet ski rental Miami",
        ),
    )

    assert buyer.is_candidate
    assert not provider.is_candidate
    assert buyer.llm_score == 0.98
    assert provider.llm_score == 0.02


def test_required_llm_skips_generic_miami_message_without_jetski_signal() -> None:
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.99),
        llm_backend=FakeLLMBackend(0.99, language="ru"),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Подскажите, что интересного есть в Майами завтра?",
            query="Русскоязычная группа Майами",
            source_language="ru",
        ),
    )

    assert not result.is_candidate
    assert result.final_score == 0.0
    assert result.embedding_score is None
    assert result.llm_score is None
    assert "prefilter:no-service-signal" in result.reasons


def test_required_llm_skips_generic_water_leisure_near_target_island() -> None:
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.99),
        llm_backend=FakeLLMBackend(0.99, language="ru"),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Подскажите, какие водные развлечения есть на Raccoon Island?",
            query="Русскоязычный чат Майами",
            source_language="ru",
        ),
    )

    assert not result.is_candidate
    assert result.embedding_score is None
    assert result.llm_score is None
    assert "prefilter:no-service-signal" in result.reasons


def test_pwc_company_name_is_not_a_watercraft_service_signal() -> None:
    result = HybridClassifier(candidate_threshold=0.5).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Ищу работу в офисе PwC Miami.",
            query="Русскоязычный чат Майами",
            source_language="ru",
        ),
    )

    assert not result.is_candidate
    assert "missing-service" in result.reasons


def test_required_llm_rejects_message_outside_profile_languages() -> None:
    english_only = replace(JETSKI_MIAMI, languages=("en",))
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.9),
        llm_backend=FakeLLMBackend(0.98, language="ru"),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        english_only,
        MessageContext(
            text="Нужно rent два гидроцикла в Miami завтра",
            query="jet ski rental Miami",
        ),
    )

    assert not result.is_candidate
    assert result.llm_score == 0.05
    assert result.extracted_data["language"] == "ru"
    assert "language:not-allowed:ru" in result.reasons


def test_required_llm_accepts_language_from_selected_group() -> None:
    slavic = replace(JETSKI_MIAMI, languages=("group:slavic",))
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.9),
        llm_backend=FakeLLMBackend(0.98, language="uk"),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        slavic,
        MessageContext(
            text="Потрібно rent два jet ski в Miami завтра",
            query="jet ski rental Miami",
        ),
    )

    assert result.is_candidate
    assert result.extracted_data["language"] == "uk"


def test_required_llm_accepts_english_message_in_russian_source() -> None:
    result = HybridClassifier(
        embedding_backend=FakeEmbeddingBackend(0.9),
        llm_backend=FakeLLMBackend(0.98, language="en"),
        candidate_threshold=0.55,
        require_llm_confirmation=True,
    ).classify(
        JETSKI_MIAMI,
        MessageContext(
            text="Need two jet skis in Miami Beach tomorrow",
            query="Russian-speaking Miami community",
            source_language="ru",
        ),
    )

    assert result.is_candidate
    assert result.extracted_data["language"] == "en"
    assert result.extracted_data["source_language"] == "ru"
    assert "language:allowed-by-source:ru" in result.reasons
