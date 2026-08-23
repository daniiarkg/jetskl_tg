from pathlib import Path

from sqlalchemy import func, select

from leadfinder.classification.types import ClassificationResult
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.ingestion import NormalizedMessage, ingest_message
from leadfinder.models import ChatSource, Lead, Signal, SourceSubscription
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
