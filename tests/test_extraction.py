from datetime import UTC, datetime

from leadfinder.extraction import extract_message_facts
from leadfinder.profiles import JETSKI_MIAMI


def test_extract_message_facts() -> None:
    result = extract_message_facts(
        JETSKI_MIAMI,
        "Looking to rent jet skis in Miami tomorrow for 4 people",
        datetime(2026, 8, 21, 12, tzinfo=UTC),
    )

    assert result["location"] == "Miami"
    assert result["intent"] == "rent"
    assert result["party_size"] == 4
    assert str(result["event_date"]).startswith("2026-08-22")
