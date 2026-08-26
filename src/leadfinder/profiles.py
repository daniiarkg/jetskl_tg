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
        "jetski",
        "jet-ski",
        "jet sky",
        "jetsky",
        "jet-sky",
        "wave runner",
        "waverunner",
        "sea-doo",
        "seadoo",
        "sea doo",
        "PWC rental",
        "personal watercraft",
        "джетски",
        "джет ски",
        "джет-ски",
        "джетскай",
        "джет скай",
        "джет-скай",
        "гидроцикл",
        "гидрик",
        "аквабайк",
        "аква байк",
        "аква-байк",
        "водный скутер",
        "jet skis",
        "jet-skis",
        "jetskis",
        "jet skys",
        "jet skies",
        "jetskys",
        "jetskies",
        "wave runners",
        "wave-runner",
        "wave-runners",
        "waverunners",
        "waveruner",
        "personal water craft",
        "PWC rentals",
        "гидроциклы",
        "гидроцикла",
        "гидроциклов",
        "гидроцикле",
        "гидроциклах",
        "гидроциклом",
        "гидроцыкл",
        "гидрацикл",
        "гидрики",
        "гидрика",
        "гидриков",
        "гидрике",
        "гидриках",
        "гидриком",
        "аквабайки",
        "аквабайка",
        "водные скутеры",
        "водного скутера",
        "водных скутеров",
        "водном скутере",
        "водными скутерами",
    ),
    locations=(
        "Miami",
        "Майами",
        "Miami Beach",
        "Майами-Бич",
        "Miami-Dade",
        "Miami Dade",
        "Майами-Дейд",
        "South Beach",
        "Саут-Бич",
        "Mid-Beach",
        "Mid Beach",
        "Мид-Бич",
        "North Beach",
        "Норт-Бич",
        "Sunny Isles",
        "Sunny Isles Beach",
        "Санни-Айлс",
        "Санни Айлс",
        "Aventura",
        "Авентура",
        "Hallandale",
        "Hallandale Beach",
        "Халландейл",
        "Холландейл",
        "Hollywood Beach",
        "Hollywood FL",
        "Hollywood Florida",
        "Холливуд-Бич",
        "Холливуд Флорида",
        "Biscayne Bay",
        "Бискейн-Бэй",
        "Key Biscayne",
        "Ки-Бискейн",
        "Ки Бискейн",
        "Haulover",
        "Haulover Beach",
        "Haulover Park",
        "Холовер",
        "Холоувер",
        "Raccoon Island",
        "Sandspur Island",
        "Ракун-Айленд",
        "Ракун Айленд",
        "остров енотов",
        "острова енотов",
        "Сэндспур-Айленд",
        "Brickell",
        "Брикелл",
        "Fort Lauderdale",
        "Форт-Лодердейл",
    ),
    intents=(
        "ищу",
        "ищем",
        "где взять",
        "где выгодно взять",
        "взять в аренду",
        "кто сдает",
        "кто сдаёт",
        "у кого контакт",
        "у кого есть контакт",
        "есть контакты",
        "подскажите",
        "посоветуйте",
        "кто посоветует",
        "аренда",
        "арендовать",
        "снять",
        "забронировать",
        "покататься",
        "на прокат",
        "нужен прокат",
        "нужна аренда",
        "интересует аренда",
        "какая цена",
        "стоимость",
        "почем",
        "почём",
        "сколько стоит",
        "хочу",
        "хотим",
        "нужно",
        "нужен",
        "нужны",
        "rent",
        "rental",
        "book",
        "available",
        "recommendation",
        "price",
        "cost",
        "rates",
        "how much",
        "who rents",
        "any contacts",
        "want",
        "need",
        "looking for",
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
        "prices from",
        "packages from",
        "our jet skis",
        "our waverunners",
        "our rentals",
        "reserve now",
        "book online",
        "booking link",
        "link in bio",
        "message us",
        "call us",
        "text us",
        "promo code",
        "use code",
        "special offer",
        "limited offer",
        "discount",
        "starting at",
        "licensed and insured",
        "delivery available",
        "now hiring",
        "job opening",
        "dealer",
        "repair",
        "parts",
        "продам",
        "продаю",
        "продается",
        "продаётся",
        "на продажу",
        "ремонт",
        "сдаю",
        "сдаем",
        "сдаём",
        "мы сдаем",
        "мы сдаём",
        "сдается",
        "предлагаем",
        "предоставляем",
        "наш прокат",
        "наш парк",
        "наши гидроциклы",
        "пишите в личку",
        "пишите в директ",
        "пишите нам",
        "напишите нам",
        "звоните",
        "обращайтесь",
        "бронируйте",
        "забронируйте",
        "открыта бронь",
        "открыто бронирование",
        "ссылка для брони",
        "промокод",
        "скидка",
        "акция",
        "спецпредложение",
        "доставка гидроциклов",
        "работаем ежедневно",
        "ищем сотрудника",
        "ищем сотрудников",
        "требуется",
        "требуются",
        "вакансия",
        "есть в наличии",
        "цены в личку",
        "лучшие цены",
        "цены от",
        "телефон для брони",
        "покатались",
        "арендовали",
        "брали в аренду",
    ),
    positive_examples=(
        "Where can I rent two jet skis in Miami tomorrow?",
        "Any recommendations for a jet ski rental in South Beach?",
        "Где арендовать гидроцикл в Майами на завтра?",
        "Подскажите, где выгодно взять jetski в Mid-Beach Miami?",
        "Хотим покататься на Sea-Doo в Майами-Бич, кого посоветуете?",
        "Ребята, кто сдаёт два jetsky в Санни-Айлс на завтра?",
        "Хотим доехать на аквабайках до острова енотов, где взять?",
    ),
    classifier_prompt=(
        "Find Russian-speaking prospective customers looking for jet ski services in the "
        "Miami area. Russian-language source context is valid audience evidence even when "
        "the message uses English product or place names. Require an explicit jet-ski, "
        "WaveRunner, Sea-Doo, aquabike or water-scooter service signal; generic requests for "
        "things to do, water activities or an island trip are not enough. Treat Miami Beach, "
        "Sunny Isles, Aventura, Hallandale, Hollywood Beach, Fort Lauderdale, Key Biscayne, "
        "Haulover, Raccoon Island and Sandspur Island as Miami-area geography. Reject "
        "non-Russian-audience sources, advertisements, providers, availability announcements, "
        "price menus, promo codes, sales, repairs, jobs, general news and past experiences "
        "without a new current or future request."
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
    # Community searches are useful for finding chats, but allowing them to consume the
    # entire limit starves direct service/location queries as profiles become more detailed.
    source_query_budget = max(1, min(30, limit // 3))
    source_queries_added = 0
    for location in profile.locations:
        for language in selected_languages:
            templates = source_templates.get(language)
            if templates is None:
                continue
            for template in templates:
                candidates.append((template.format(location=location), "source"))
                source_queries_added += 1
                if source_queries_added >= source_query_budget:
                    break
            if source_queries_added >= source_query_budget:
                break
        if source_queries_added >= source_query_budget:
            break

    # Cover every geography and every vocabulary variant before expanding the full matrix.
    # This keeps rare colloquialisms and Miami-area neighborhoods inside the normal 120-query
    # budget instead of filling it with combinations from only the first location.
    primary_service = profile.services[0]
    primary_location = profile.locations[0]
    for location in profile.locations:
        candidates.append((f"{primary_service} {location}", "service"))
    service_query_budget = max(1, min(len(profile.services), limit // 4))
    for service in profile.services[:service_query_budget]:
        candidates.append((f"{service} {primary_location}", "service"))

    for intent in profile.intents:
        candidates.append((f"{intent} {primary_service} {primary_location}", "intent"))

    for location in profile.locations:
        for service in profile.services:
            candidates.append((f"{service} {location}", "service"))

    for location in profile.locations:
        for intent in profile.intents:
            candidates.append((f"{intent} {primary_service} {location}", "intent"))

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
