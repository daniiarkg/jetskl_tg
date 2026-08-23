import csv
from pathlib import Path

from sqlalchemy import func, select

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.exporter import export_leads
from leadfinder.models import ChatSource, Lead, SearchProfileRecord, Signal
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile
from leadfinder.services import (
    acquire_job_lease,
    create_or_update_lead_from_signal,
    record_call_consent,
    release_job_lease,
    review_signal,
)


def test_signal_creates_lead_and_consent_controls_csv(tmp_path: Path) -> None:
    database = Database(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}"))
    database.create_all()
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        source = ChatSource(
            telegram_chat_id=-100123,
            title="Miami visitors",
            username="miami_visitors",
        )
        session.add(source)
        session.flush()
        signal = Signal(
            profile_id=profile.id,
            source_id=source.id,
            telegram_message_id=77,
            text="Need two jet skis in Miami tomorrow",
            author_user_id=456,
            author_username="guest456",
            author_display_name="Guest",
            author_phone_visible="+13055550123",
            final_score=0.91,
            extracted_data={"location": "Miami", "party_size": 2, "language": "en"},
        )
        session.add(signal)
        session.flush()
        lead = create_or_update_lead_from_signal(session, signal)
        assert lead is not None
        assert lead.language == "en"
        lead_id = lead.id

    output = tmp_path / "leads-without-consent.csv"
    assert export_leads(database, output) == 1
    with output.open(encoding="utf-8-sig") as fp:
        row = next(csv.DictReader(fp))
    assert row["phone"] == ""
    assert row["language"] == "en"
    assert row["message_permalink"] == ""

    with database.session() as session:
        lead = session.get(Lead, lead_id)
        assert lead is not None
        record_call_consent(session, lead, True, "Asked us to call in Telegram")

    output = tmp_path / "leads-with-consent.csv"
    export_leads(database, output)
    with output.open(encoding="utf-8-sig") as fp:
        row = next(csv.DictReader(fp))
    assert row["phone"] == "+13055550123"
    assert row["source_title"] == "Miami visitors"

    with database.session() as session:
        assert session.scalar(select(SearchProfileRecord.slug)) == "jetski-miami"


def test_job_lease_is_exclusive_and_releasable(tmp_path: Path) -> None:
    database = Database(Settings(database_url=f"sqlite:///{tmp_path / 'lease.db'}"))
    database.create_all()
    with database.session() as session:
        assert acquire_job_lease(session, "telegram_session", "worker-1")
    with database.session() as session:
        assert not acquire_job_lease(session, "telegram_session", "worker-2")
    with database.session() as session:
        release_job_lease(session, "telegram_session", "worker-1")
    with database.session() as session:
        assert acquire_job_lease(session, "telegram_session", "worker-2")


def test_rejecting_only_signal_removes_unconfirmed_automatic_lead(tmp_path: Path) -> None:
    database = Database(Settings(database_url=f"sqlite:///{tmp_path / 'reject.db'}"))
    database.create_all()
    with database.session() as session:
        profile = upsert_profile(session, JETSKI_MIAMI, query_limit=3)
        source = ChatSource(telegram_chat_id=-100555, title="Русские Майами")
        session.add(source)
        session.flush()
        signal = Signal(
            profile_id=profile.id,
            source_id=source.id,
            telegram_message_id=101,
            text="jetski Miami?",
            author_user_id=800,
            final_score=0.8,
            status="new",
        )
        session.add(signal)
        session.flush()
        lead = create_or_update_lead_from_signal(session, signal)
        assert lead is not None
        review_signal(session, signal, "rejected", "Not a buyer")

    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(Lead)) == 0
