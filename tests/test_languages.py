import pytest

from leadfinder.languages import (
    expand_language_filters,
    language_is_allowed,
    normalize_language_filters,
)


def test_language_filters_normalize_codes_aliases_and_groups() -> None:
    assert normalize_language_filters(["RU", "slavic", "russian", "group:slavic"]) == (
        "ru",
        "group:slavic",
    )


def test_language_group_expands_to_iso_codes() -> None:
    expanded = expand_language_filters(("group:slavic",))

    assert expanded is not None
    assert {"ru", "uk", "pl"}.issubset(expanded)
    assert language_is_allowed(("group:slavic",), "Russian")
    assert not language_is_allowed(("en", "es"), "ru")


def test_empty_or_any_language_filter_allows_all_languages() -> None:
    assert expand_language_filters(()) is None
    assert normalize_language_filters(("any",)) == ()
    assert language_is_allowed((), "ja")


def test_unknown_language_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported language filter"):
        normalize_language_filters(("klingon",))
