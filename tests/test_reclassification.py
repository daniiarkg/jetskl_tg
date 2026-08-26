from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from leadfinder.classification.types import ClassificationResult
from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import ChatSource, Lead, Signal
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.reclassification import reclassify_pending_signals
from leadfinder.repository import upsert_profile


class FakeCandidateClassifier:
    def classify(self, _profile, _context) -> ClassificationResult:
        return ClassificationResult(
            is_candidate=True,
            final_score=0.92,
            keyword_score=0.8,
            embedding_score=0.9,
            llm_score=0.96,
            reasons=("gemini:buyer",),
            extracted_data={"intent": "rent", "location": "Miami", "language": "ru"},
        )


def test_reclassification_cannot_revive_stale_signal_as_automatic_lead(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stale-reclassification.db'}",
        auto_create_leads=True,
    )
    database = Database(settings)
    database.create_all()
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        source = ChatSource(
            telegram_chat_id=-100800,
            username="old_miami_group",
            title="Old Miami Group",
            kind="group",
            language="ru",
        )
        session.add(source)
        session.flush()
        signal = Signal(
            profile_id=profile.id,
            source_id=source.id,
            telegram_message_id=900,
            message_date=datetime.now(UTC) - timedelta(days=91),
            text="Где арендовать гидроцикл в Майами?",
            author_user_id=42,
            author_display_name="Old Buyer",
            author_is_bot=False,
            final_score=0.4,
            status="possible",
        )
        session.add(signal)

    summary = reclassify_pending_signals(
        settings,
        database,
        "jetski-miami",
        FakeCandidateClassifier(),
    )

    assert summary.candidates_confirmed == 1
    assert summary.leads_created_or_updated == 0
    with database.session() as session:
        signal = session.scalar(select(Signal))
        assert signal is not None and signal.status == "possible"
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
