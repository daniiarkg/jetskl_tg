from __future__ import annotations

import math

import httpx

from leadfinder.classification.schemas import LeadDecisionPayload, lead_probability
from leadfinder.classification.types import LLMDecision, MessageContext
from leadfinder.languages import language_filter_description
from leadfinder.profiles import SearchProfileSpec


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise RuntimeError("Gemini returned incompatible embedding vectors")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


class GeminiEmbeddingBackend:
    """Semantic retrieval through the official Gemini REST API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        output_dimensionality: int = 768,
        timeout_seconds: float = 45.0,
    ):
        self._client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            headers={"x-goog-api-key": api_key},
            timeout=timeout_seconds,
        )
        self.model = model
        self.output_dimensionality = output_dimensionality
        self._query_cache: dict[str, list[float]] = {}

    def _embed(self, text: str) -> list[float]:
        response = self._client.post(
            f"/models/{self.model}:embedContent",
            json={
                "content": {"parts": [{"text": text}]},
                "output_dimensionality": self.output_dimensionality,
            },
        )
        _raise_gemini_error(response)
        payload = response.json()
        embedding = payload.get("embedding") or {}
        embeddings = payload.get("embeddings") or []
        values = embedding.get("values")
        if not values and embeddings:
            values = embeddings[0].get("values")
        if not values:
            raise RuntimeError("Gemini returned no embedding")
        return [float(value) for value in values]

    def similarity(self, query: str, text: str) -> float:
        query_prompt = (
            "Task: semantic retrieval. Represent this search intent for matching public "
            "Telegram messages written by prospective customers. Search intent: "
            f"{query}"
        )
        query_vector = self._query_cache.get(query_prompt)
        if query_vector is None:
            query_vector = self._embed(query_prompt)
            self._query_cache[query_prompt] = query_vector
        document_vector = self._embed(
            "Task: semantic retrieval. Represent this public Telegram message as a document "
            "to match against a prospective-customer search intent. Message: "
            f"{text}"
        )
        return _cosine_similarity(query_vector, document_vector)


class GeminiLLMBackend:
    """High-precision final decision through Gemini structured output."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 45.0):
        self._client = httpx.Client(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            headers={"x-goog-api-key": api_key},
            timeout=timeout_seconds,
        )
        self.model = model

    def classify(
        self,
        profile: SearchProfileSpec,
        context: MessageContext,
    ) -> LLMDecision:
        prompt = f"""
You classify public Telegram messages for a human-reviewed lead queue.

Search definition:
{profile.classifier_prompt}

Allowed message languages for this search profile:
{language_filter_description(profile.languages)}

Known source language (audience evidence; may be unknown):
{context.source_language or "unknown"}

Discovery or chat context (supporting evidence only; it cannot replace service intent):
{context.query}

Required decision rules:
- is_potential_customer=true only when the author appears to be personally seeking,
  comparing, asking for recommendations for, or trying to book the configured service.
- The requested geography must be explicit or strongly implied by the message/chat context.
- Reject provider advertisements, promotional posts, offers, sales, repairs, jobs, news,
  unrelated rentals and vague social chatter.
- A provider ad is never a potential customer, even if it contains every search keyword.
- Do not infer identity, party size, date or location unless explicitly present.
- Return language as a lowercase ISO 639-1 code. Detect code-switching by returning the
  dominant language.
- English service and place names inside a Russian message do not make it non-Russian.
- When the known source language is Russian, an English-only message can still belong to the
  Russian-speaking target audience, but customer intent and service demand remain mandatory.
- Keep reason short and factual.

Telegram message:
{context.text}
""".strip()
        response = self._client.post(
            f"/models/{self.model}:generateContent",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": LeadDecisionPayload.model_json_schema(),
                },
            },
        )
        _raise_gemini_error(response)
        response_payload = response.json()
        try:
            output_text = response_payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Gemini returned no structured classification") from None
        payload = LeadDecisionPayload.model_validate_json(output_text)

        score = lead_probability(payload)
        extracted = {
            key: value
            for key, value in {
                "intent": payload.intent,
                "location": payload.location,
                "event_date_text": payload.event_date_text,
                "party_size": payload.party_size,
                "language": payload.language,
            }.items()
            if value is not None
        }
        return LLMDecision(
            score=max(0.0, min(1.0, score)),
            reasons=(f"gemini:{payload.reason[:240]}",),
            extracted_data=extracted,
        )


def _raise_gemini_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        message = str(response.json().get("error", {}).get("message", "request failed"))
    except (TypeError, ValueError):
        message = "request failed"
    raise RuntimeError(f"Gemini API error {response.status_code}: {message[:300]}")
