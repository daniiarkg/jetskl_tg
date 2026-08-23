from __future__ import annotations

from dataclasses import dataclass

from leadfinder.languages import query_languages


@dataclass(frozen=True, slots=True)
class SearchProfileSpec:
    slug: str
    name: str
    description: str
    services: tuple[str, ...]
    locations: tuple[str, ...]
    intents: tuple[str, ...]
    languages: tuple[str, ...]
    negative_terms: tuple[str, ...]
    positive_examples: tuple[str, ...]
    classifier_prompt: str


JETSKI_MIAMI = SearchProfileSpec(
    slug="jetski-miami",
    name="Русскоязычный спрос на jetski в Майами",
    description=(
        "Русскоязычные пользователи, которые хотят арендовать или забронировать "
        "гидроцикл в районе Майами."
    ),
    services=(
        "jet ski",
        "jet skis",
        "jetski",
        "jetskis",
        "wave runner",
        "wave runners",
        "waverunner",
        "waverunners",
        "personal watercraft",
        "PWC rental",
        "sea-doo",
        "seadoo",
        "джетски",
        "джет ски",
        "джет-ски",
        "гидроцикл",
        "гидроциклы",
        "гидроцикла",
        "гидроциклов",
        "гидроцикле",
        "гидроциклах",
        "гидроциклом",
        "гидрик",
        "гидрики",
        "гидрика",
        "гидриков",
        "гидрике",
        "гидриках",
        "гидриком",
        "водный скутер",
        "водные скутеры",
        "водного скутера",
        "водных скутеров",
        "водном скутере",
        "водными скутерами",
    ),
    locations=(
        "Miami",
        "Miami Beach",
        "South Beach",
        "Biscayne Bay",
        "Key Biscayne",
        "Haulover",
        "Fort Lauderdale",
        "Майами",
        "Майами-Бич",
        "Мид-Бич",
        "Саут-Бич",
        "Форт-Лодердейл",
    ),
    intents=(
        "rent",
        "rental",
        "book",
        "available",
        "recommendation",
        "price",
        "want",
        "need",
        "looking for",
        "аренда",
        "арендовать",
        "снять",
        "забронировать",
        "покататься",
        "посоветуйте",
        "подскажите",
        "где взять",
        "сколько стоит",
        "хочу",
        "нужно",
        "ищу",
    ),
    # The target audience is Russian-speaking. English product/place names are
    # still valid inside Russian messages or in explicitly Russian sources.
    languages=("ru",),
    negative_terms=(
        "for sale",
        "selling my",
        "we offer",
        "our fleet",
        "dm for booking",
        "book now",
        "contact us",
        "dm me",
        "we rent",
        "rentals available",
        "hourly rental",
        "best rates",
        "dealer",
        "repair",
        "parts",
        "продам",
        "ремонт",
        "сдаю",
        "сдается",
        "предлагаем",
        "пишите в личку",
        "бронируйте",
        "есть в наличии",
        "цены в личку",
    ),
    positive_examples=(
        "Where can I rent two jet skis in Miami tomorrow?",
        "Any recommendations for a jet ski rental in South Beach?",
        "Где арендовать гидроцикл в Майами на завтра?",
        "Подскажите, где выгодно взять jetski в Mid-Beach Miami?",
        "Хотим покататься на Sea-Doo в Майами-Бич, кого посоветуете?",
    ),
    classifier_prompt=(
        "Find Russian-speaking prospective customers looking for jet ski services in the "
        "Miami area. Russian-language source context is valid audience evidence even when "
        "the message uses English product or place names. Reject non-Russian-audience sources, "
        "advertisements, providers, sales, repairs and general news."
    ),
)


def generate_queries(profile: SearchProfileSpec, limit: int = 120) -> list[str]:
    """Generate a diverse deterministic query set without a full Cartesian product."""
    return [query for query, _query_type in generate_query_specs(profile, limit)]


def generate_query_specs(
    profile: SearchProfileSpec,
    limit: int = 120,
) -> list[tuple[str, str]]:
    """Prioritize communities where buyers gather, then direct service searches."""
    candidates: list[tuple[str, str]] = []

    candidates.append((f"{profile.services[0]} {profile.locations[0]}", "service"))

    source_templates = {
        "en": (
            "{location} community",
            "{location} travel",
            "{location} vacation",
            "things to do {location}",
            "{location} tourists",
        ),
        "es": (
            "comunidad {location}",
            "viaje a {location}",
            "vacaciones en {location}",
            "qué hacer en {location}",
            "turistas en {location}",
        ),
        "pt": (
            "comunidade {location}",
            "viagem para {location}",
            "férias em {location}",
            "o que fazer em {location}",
        ),
        "ru": (
            "{location} сообщество",
            "отдых в {location}",
            "путешествие в {location}",
            "развлечения в {location}",
            "{location} туристы",
        ),
        "uk": (
            "{location} спільнота",
            "відпочинок у {location}",
            "подорож до {location}",
        ),
        "fr": (
            "communauté {location}",
            "voyage à {location}",
            "vacances à {location}",
        ),
    }
    selected_languages = query_languages(profile.languages)
    for location in profile.locations:
        for language in selected_languages:
            templates = source_templates.get(language)
            if templates is None:
                continue
            candidates.extend(
                (template.format(location=location), "source") for template in templates
            )

    for location in profile.locations:
        for service in profile.services:
            candidates.append((f"{service} {location}", "service"))

    primary_service = profile.services[0]
    for location in profile.locations:
        for intent in profile.intents:
            candidates.append((f"{intent} {primary_service} {location}", "intent"))

    primary_location = profile.locations[0]
    broad_templates = {
        "en": ("{location} water activities", "{location} vacation activities"),
        "es": ("actividades acuáticas {location}", "qué hacer en {location}"),
        "pt": ("atividades aquáticas {location}", "o que fazer em {location}"),
        "ru": ("водные развлечения {location}", "развлечения в {location}"),
        "uk": ("водні розваги {location}",),
        "fr": ("activités nautiques {location}",),
    }
    for language in selected_languages:
        for template in broad_templates.get(language, ()):
            candidates.append((template.format(location=primary_location), "source"))

    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for query, query_type in candidates:
        normalized = " ".join(query.casefold().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append((query, query_type))
        if len(unique) >= limit:
            break
    return unique
