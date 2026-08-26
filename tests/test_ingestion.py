from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from leadfinder.classification.types import ClassificationResult
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.ingestion import NormalizedMessage, ingest_message
from leadfinder.models import (
    ChatSource,
    Lead,
    NotificationOutbox,
    NotificationSubscriber,
    Signal,
    SourceSubscription,
)
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile
from leadfinder.services import set_subscription_status


class FakeClassifier:
    def classify(self, _profile, _context) -> ClassificationResult:
        return ClassificationResult(
            is_candidate=True,
            final_score=0.91,
            keyword_score=0.8,
            embedding_score=0.88,
            llm_score=0.96,
            reasons=("gemini:buyer",),
            extracted_data={"intent": "rent", "location": "Miami", "language": "en"},
        )


class FakeNonCandidateClassifier:
    def classify(self, _profile, _context) -> ClassificationResult:
        return ClassificationResult(
            is_candidate=False,
            final_score=0.50,
            keyword_score=0.5,
            reasons=("not-a-buyer",),
            extracted_data={"language": "ru"},
        )


def test_external_connector_requires_source_approval_and_creates_lead(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ingest.db'}",
        auto_create_leads=True,
    )
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, query_limit=3)

    message = NormalizedMessage(
        platform="forum",
        source_external_id="miami-travel/water-activities",
        source_title="Miami Travel Forum",
        source_url="https://example.test/miami",
        message_external_id="post-42",
        message_url="https://example.test/miami/post-42",
        text="Where can I rent two jet skis in Miami tomorrow?",
        published_at=datetime.now(UTC),
        author_external_id="member-9",
        author_username="traveler9",
    )
    first = ingest_message(
        settings, database, "jetski-miami", FakeClassifier(), message
    )
    assert first.classification_skipped
    assert first.signal_id is None

    with database.session() as session:
        subscription = session.scalar(select(SourceSubscription))
        assert subscription is not None
        set_subscription_status(session, subscription, "approved")

    second = ingest_message(
        settings, database, "jetski-miami", FakeClassifier(), message
    )
    assert second.signal_id is not None
    assert second.lead_id is not None

    with database.session() as session:
        source = session.scalar(select(ChatSource))
        lead = session.scalar(select(Lead))
        assert source is not None and source.platform == "forum"
        assert lead is not None and lead.platform == "forum"
        assert lead.external_user_id == "member-9"
        assert lead.language == "en"
        assert session.scalar(select(func.count()).select_from(Signal)) == 1

    duplicate = ingest_message(
        settings, database, "jetski-miami", FakeClassifier(), message
    )
    assert duplicate.duplicate


def test_external_connector_missing_date_cannot_create_lead_or_notification(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'missing-date.db'}",
        auto_create_leads=True,
    )
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        session.add(NotificationSubscriber(telegram_chat_id=500, active=True))

    message = NormalizedMessage(
        platform="forum",
        source_external_id="unknown-date-source",
        source_title="Unknown Date Source",
        message_external_id="post-1",
        text="Где арендовать гидроцикл в Майами?",
        author_external_id="member-1",
    )
    assert ingest_message(
        settings, database, "jetski-miami", FakeClassifier(), message
    ).classification_skipped
    with database.session() as session:
        subscription = session.scalar(select(SourceSubscription))
        assert subscription is not None
        set_subscription_status(session, subscription, "approved")

    result = ingest_message(
        settings, database, "jetski-miami", FakeClassifier(), message
    )

    assert result.signal_id is not None
    assert result.lead_id is None
    with database.session() as session:
        signal = session.get(Signal, result.signal_id)
        assert signal is not None and signal.message_date is None
        assert signal.status == "possible"
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
        assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0


def test_duplicate_non_candidate_cannot_create_lead(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'duplicate-non-candidate.db'}",
        auto_create_leads=True,
    )
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, query_limit=3)

    message = NormalizedMessage(
        platform="forum",
        source_external_id="general-miami",
        source_title="General Miami",
        message_external_id="post-2",
        text="Куда сходить в Майами?",
        published_at=datetime.now(UTC),
        author_external_id="member-2",
    )
    assert ingest_message(
        settings, database, "jetski-miami", FakeNonCandidateClassifier(), message
    ).classification_skipped
    with database.session() as session:
        subscription = session.scalar(select(SourceSubscription))
        assert subscription is not None
        set_subscription_status(session, subscription, "approved")

    first = ingest_message(
        settings, database, "jetski-miami", FakeNonCandidateClassifier(), message
    )
    duplicate = ingest_message(
        settings, database, "jetski-miami", FakeNonCandidateClassifier(), message
    )

    assert first.signal_id is not None and first.lead_id is None
    assert duplicate.duplicate and duplicate.lead_id is None
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
