from __future__ import annotations

import re
from collections.abc import Iterable

LANGUAGE_GROUPS: dict[str, frozenset[str]] = {
    "miami": frozenset({"en", "es", "pt", "ru"}),
    "slavic": frozenset(
        {"ru", "uk", "be", "pl", "cs", "sk", "bg", "sr", "hr", "bs", "mk", "sl"}
    ),
    "romance": frozenset({"es", "pt", "fr", "it", "ro", "ca"}),
    "germanic": frozenset({"en", "de", "nl", "sv", "no", "da", "is"}),
    "turkic": frozenset({"tr", "az", "kk", "ky", "uz", "tk"}),
    "east-asian": frozenset({"zh", "ja", "ko"}),
    "middle-east": frozenset({"ar", "he", "fa"}),
}

LANGUAGE_ALIASES = {
    "english": "en",
    "spanish": "es",
    "russian": "ru",
    "portuguese": "pt",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "ukrainian": "uk",
    "polish": "pl",
    "arabic": "ar",
    "hebrew": "he",
    "chinese": "zh",
    "japanese": "ja",
    "korean": "ko",
}

ANY_LANGUAGE_TOKENS = {"", "*", "all", "any", "любой", "все"}


def _normalize_token(value: str) -> str:
    return value.casefold().strip().replace("_", "-")


def normalize_language_code(value: str | None) -> str | None:
    if not value:
        return None
    token = _normalize_token(value)
    token = LANGUAGE_ALIASES.get(token, token)
    if token.startswith("group:"):
        return None
    base = token.split("-", 1)[0]
    return base if re.fullmatch(r"[a-z]{2,3}", base) else None


def normalize_language_filters(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = _normalize_token(str(value))
        if token in ANY_LANGUAGE_TOKENS:
            return ()
        group_name = token.removeprefix("group:")
        if group_name in LANGUAGE_GROUPS:
            canonical = f"group:{group_name}"
        else:
            code = normalize_language_code(token)
            if code is None:
                supported = ", ".join(f"group:{name}" for name in LANGUAGE_GROUPS)
                raise ValueError(
                    f"Unsupported language filter '{value}'. Use ISO codes or: {supported}"
                )
            canonical = code
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return tuple(normalized)


def expand_language_filters(values: Iterable[str]) -> frozenset[str] | None:
    filters = normalize_language_filters(values)
    if not filters:
        return None
    expanded: set[str] = set()
    for token in filters:
        if token.startswith("group:"):
            expanded.update(LANGUAGE_GROUPS[token.removeprefix("group:")])
        else:
            expanded.add(token)
    return frozenset(expanded)


def language_is_allowed(values: Iterable[str], detected: str | None) -> bool:
    allowed = expand_language_filters(values)
    if allowed is None or not detected:
        return True
    code = normalize_language_code(detected)
    return code is None or code in allowed


def language_filter_description(values: Iterable[str]) -> str:
    filters = normalize_language_filters(values)
    if not filters:
        return "any language"
    expanded = expand_language_filters(filters) or frozenset()
    return f"{', '.join(filters)} (ISO codes: {', '.join(sorted(expanded))})"


def query_languages(values: Iterable[str]) -> tuple[str, ...]:
    expanded = expand_language_filters(values)
    if expanded is None:
        return ("en", "es", "pt", "ru")
    return tuple(sorted(expanded))
