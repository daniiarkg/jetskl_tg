from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from leadfinder.api import create_app
from leadfinder.config import get_settings
from leadfinder.db import Database
from leadfinder.models import ChatSource, SearchProfileRecord, Signal
from leadfinder.services import review_signal


def test_dashboard_health_and_profile_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_ACCESS_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert "Demand Leadfinder" in client.get("/").text
            profiles = client.get("/api/profiles").json()
            assert profiles[0]["slug"] == "jetski-miami"
            assert profiles[0]["languages"] == ["ru"]
            assert "ISO codes" in profiles[0]["language_filter"]
            assert client.get("/api/stats").json()["sources"] == 0
            notification_status = client.get("/api/notifications/status").json()
            assert not notification_status["bot_configured"]
            assert notification_status["active_subscribers"] == 0

            response = client.post(
                "/api/profiles",
                json={
                    "slug": "boat-rental-miami",
                    "name": "Boat rental Miami",
                    "services": ["boat rental"],
                    "locations": ["Miami"],
                    "intents": ["rent", "book"],
                    "languages": ["en"],
                    "negative_terms": ["we offer"],
                    "positive_examples": ["Where can I rent a boat in Miami?"],
                    "classifier_prompt": "Find prospective boat-rental customers in Miami.",
                    "query_limit": 10,
                },
            )
            assert response.status_code == 200
            assert response.json()["slug"] == "boat-rental-miami"
            assert client.get("/api/stats").json()["profiles"] == 2

            invalid = client.post(
                "/api/profiles",
                json={
                    "slug": "invalid-language",
                    "name": "Invalid language",
                    "services": ["boat rental"],
                    "locations": ["Miami"],
                    "intents": ["rent"],
                    "languages": ["klingon"],
                    "classifier_prompt": "Find boat-rental demand.",
                },
            )
            assert invalid.status_code == 422
    finally:
        get_settings.cache_clear()


def test_dashboard_access_key_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'protected-api.db'}")
    monkeypatch.setenv("ADMIN_API_KEY", "panel-secret")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_ACCESS_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/auth/status").json() == {"required": True}
            assert client.get("/api/stats").status_code == 401
            assert client.post(
                "/api/auth/login",
                json={"access_key": "wrong"},
            ).status_code == 401
            assert client.post(
                "/api/auth/login",
                json={"access_key": "panel-secret"},
            ).json() == {"ok": True}
            assert client.get(
                "/api/stats",
                headers={"X-Admin-Key": "panel-secret"},
            ).status_code == 200

            dashboard = client.get("/").text
            assert 'id="auth-screen"' in dashboard
            assert "sessionStorage.getItem('leadfinderAdminKey')" in dashboard
            assert "localStorage.setItem('leadfinderAdminKey'" not in dashboard
            assert "Комментарий к решению" not in dashboard
            assert "Телефон (только полученный" not in dashboard
            assert "На чём основано согласие" not in dashboard
            assert 'onclick="editPhone(' not in dashboard
            assert 'onclick="consent(' not in dashboard
            assert "JSON.stringify({status,note:''})" in dashboard
    finally:
        get_settings.cache_clear()


def test_lead_api_includes_source_message_date(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lead-date.db'}")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_NOTIFICATION_ACCESS_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            database = Database(get_settings())
            with database.session() as session:
                profile = session.scalar(
                    select(SearchProfileRecord).where(
                        SearchProfileRecord.slug == "jetski-miami"
                    )
                )
                assert profile is not None
                source = ChatSource(
                    telegram_chat_id=-100123,
                    title="Miami test chat",
                )
                session.add(source)
                session.flush()
                signal = Signal(
                    profile_id=profile.id,
                    source_id=source.id,
                    telegram_message_id=321,
                    message_date=datetime(2026, 8, 23, 12, 34, tzinfo=UTC),
                    text="Где взять гидроцикл в аренду в Майами?",
                    author_user_id=777,
                    final_score=0.91,
                    status="new",
                    extracted_data={"intent": "rent", "location": "Miami"},
                )
                session.add(signal)
                session.flush()
                lead = review_signal(session, signal, "qualified", "")
                assert lead is not None

            leads = client.get("/api/leads").json()
            assert leads[0]["message_date"].startswith("2026-08-23T12:34:00")
            dashboard = client.get("/").text
            assert "Дата сообщения" in dashboard
            assert "fmtDate(x.message_date)" in dashboard
    finally:
        get_settings.cache_clear()
