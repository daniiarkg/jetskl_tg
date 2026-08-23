from dataclasses import replace

from leadfinder.profiles import JETSKI_MIAMI, generate_queries


def test_generate_queries_is_unique_and_bounded() -> None:
    queries = generate_queries(JETSKI_MIAMI, limit=25)

    assert len(queries) == 25
    assert len({query.casefold() for query in queries}) == 25
    assert "jet ski Miami" in queries


def test_generate_queries_targets_russian_audience_and_mixed_vocabulary() -> None:
    queries = generate_queries(JETSKI_MIAMI, limit=120)

    assert any("сообщество" in query for query in queries)
    assert any("Майами" in query for query in queries)
    assert not any("moto de agua" in query for query in queries)


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
