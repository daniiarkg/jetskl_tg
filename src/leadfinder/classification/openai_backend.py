from __future__ import annotations

from typing import Any

from leadfinder.classification.schemas import LeadDecisionPayload, lead_probability
from leadfinder.classification.types import LLMDecision, MessageContext
from leadfinder.profiles import SearchProfileSpec


class OpenAILLMBackend:
    """Optional final-stage classifier using OpenAI Structured Outputs."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 30.0):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI classification is enabled but the AI extra is not installed. "
                "Run: uv sync --extra llm"
            ) from exc
        self._client: Any = OpenAI(api_key=api_key, timeout=timeout_seconds)
        self.model = model

    def classify(
        self,
        profile: SearchProfileSpec,
        context: MessageContext,
    ) -> LLMDecision:
        system_prompt = (
            "You classify public Telegram messages for a lead-review queue. "
            "Return true only when the message author appears to be a prospective customer. "
            "Reject provider advertisements, job posts, sales, repairs, news and unclear chatter. "
            "Do not infer or invent personal information. Extract only facts explicitly present "
            "in the message. "
            f"Search definition: {profile.classifier_prompt}"
        )
        response = self._client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context.text},
            ],
            text_format=LeadDecisionPayload,
        )
        payload = response.output_parsed
        if payload is None:
            raise RuntimeError("The LLM returned no structured classification")

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
            reasons=(f"llm:{payload.reason[:240]}",),
            extracted_data=extracted,
        )
