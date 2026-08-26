from dataclasses import replace

from leadfinder.profiles import JETSKI_MIAMI, generate_queries, generate_query_specs


def test_generate_queries_is_unique_and_bounded() -> None:
    queries = generate_queries(JETSKI_MIAMI, limit=25)

    assert len(queries) == 25
    assert len({query.casefold() for query in queries}) == 25
    assert "jet ski Miami" in queries


def test_generate_queries_targets_russian_audience_and_mixed_vocabulary() -> None:
    queries = generate_queries(JETSKI_MIAMI, limit=120)

    assert any("сообщество" in query for query in queries)
    assert any("Майами" in query for query in queries)
    assert "jetsky Miami" in queries
    assert "аквабайк Miami" in queries
    assert "jet ski Sunny Isles" in queries
    assert "jet ski Raccoon Island" in queries
    assert "jet ski остров енотов" in queries
    assert "кто сдаёт jet ski Miami" in queries
    assert not any("moto de agua" in query for query in queries)


def test_generate_queries_balances_source_service_and_buyer_intent() -> None:
    query_specs = generate_query_specs(JETSKI_MIAMI, limit=120)
    query_types = {query_type for _query, query_type in query_specs}

    assert query_types == {"source", "service", "intent"}
    assert sum(query_type == "source" for _query, query_type in query_specs) <= 30
    assert JETSKI_MIAMI.languages == ("ru",)


def test_generate_queries_uses_selected_language_for_community_discovery() -> None:
    profile = replace(JETSKI_MIAMI, languages=("ru",))
    queries = generate_queries(profile, limit=40)

    assert any("сообщество" in query for query in queries)
    assert not any(query.startswith("comunidad ") for query in queries)


def test_generate_queries_expands_language_group() -> None:
    profile = replace(JETSKI_MIAMI, languages=("group:romance",))
    queries = generate_queries(profile, limit=80)

    assert any(query.startswith("comunidad ") for query in queries)
    assert any(query.startswith("communauté ") for query in queries)
