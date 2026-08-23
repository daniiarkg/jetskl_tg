from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from leadfinder.profiles import SearchProfileSpec

_PARTY_PATTERNS = (
    re.compile(r"\b(?:for|party of|group of)\s+(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s+(?:people|persons|guests|personas|человек)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,3})\s+(?:jet\s*skis?|waverunners?|гидроцикл\w*)\b", re.IGNORECASE),
)


def extract_message_facts(
    profile: SearchProfileSpec,
    text: str,
    message_date: datetime | None,
) -> dict[str, object]:
    normalized = text.casefold()
    facts: dict[str, object] = {}

    for location in profile.locations:
        if location.casefold() in normalized:
            facts["location"] = location
            break
    for intent in profile.intents:
        if intent.casefold() in normalized:
            facts["intent"] = intent
            break
    for pattern in _PARTY_PATTERNS:
        match = pattern.search(text)
        if match:
            facts["party_size"] = int(match.group(1))
            break

    reference = message_date or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    day_offset: int | None = None
    if any(word in normalized for word in ("tomorrow", "mañana", "завтра")):
        day_offset = 1
    elif any(word in normalized for word in ("today", "hoy", "сегодня")):
        day_offset = 0
    if day_offset is not None:
        event_date = (reference + timedelta(days=day_offset)).astimezone(UTC)
        facts["event_date"] = event_date.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
    return facts
