from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.notifications"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int | None = None
    telegram_api_hash: SecretStr | None = None
    telegram_session_path: Path = Path(".sessions/leadfinder")
    telegram_session_string: SecretStr | None = None

    database_url: str = "sqlite:///data/leadfinder.db"

    discovery_max_queries: int = Field(default=120, ge=1, le=500)
    discovery_results_per_query: int = Field(default=20, ge=1, le=100)
    discovery_message_lookback_days: int = Field(default=30, ge=1, le=365)
    discovery_source_sample_messages: int = Field(default=50, ge=1, le=500)
    monitor_interval_seconds: int = Field(default=60, ge=30, le=86400)
    monitor_messages_per_source: int = Field(default=200, ge=1, le=5000)
    signal_store_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    lead_candidate_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    auto_create_leads: bool = False
    max_signal_text_length: int = Field(default=4000, ge=200, le=50000)

    gemini_api_key: SecretStr | None = None
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_embedding_dimensions: int = Field(default=768, ge=128, le=3072)
    gemini_llm_model: str = "gemini-3.7-flash"
    gemini_timeout_seconds: float = Field(default=45.0, ge=1.0, le=180.0)

    server_host: str = "127.0.0.1"
    server_port: int = Field(default=8000, ge=1, le=65535)
    admin_api_key: SecretStr | None = None
    telegram_notification_bot_token: SecretStr | None = None
    telegram_notification_access_key: SecretStr | None = None
    notification_delivery_batch: int = Field(default=50, ge=1, le=500)
    notification_max_attempts: int = Field(default=8, ge=1, le=50)
    lead_hot_max_age_days: int = Field(default=7, ge=1, le=3650)
    lead_active_max_age_days: int = Field(default=30, ge=1, le=3650)
    lead_review_max_age_days: int = Field(default=90, ge=1, le=3650)
    backfill_notifications_enabled: bool = False
    dashboard_public_url: str = "http://127.0.0.1:8000"

    @field_validator(
        "telegram_api_hash",
        "telegram_session_string",
        "gemini_api_key",
        "admin_api_key",
        "telegram_notification_bot_token",
        "telegram_notification_access_key",
        mode="before",
    )
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def freshness_windows_are_ordered(self) -> Settings:
        if not (
            self.lead_hot_max_age_days
            <= self.lead_active_max_age_days
            <= self.lead_review_max_age_days
        ):
            raise ValueError(
                "Freshness windows must satisfy "
                "LEAD_HOT_MAX_AGE_DAYS <= LEAD_ACTIVE_MAX_AGE_DAYS "
                "<= LEAD_REVIEW_MAX_AGE_DAYS"
            )
        return self

    def require_telegram_credentials(self) -> tuple[int, str]:
        if not self.telegram_api_id or not self.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        return self.telegram_api_id, self.telegram_api_hash.get_secret_value()

    def require_gemini_api_key(self) -> str:
        if self.gemini_api_key is None:
            raise RuntimeError(
                "GEMINI_API_KEY must be set in .env; Gemini embeddings and LLM are required"
            )
        return self.gemini_api_key.get_secret_value()

    @property
    def effective_admin_api_key(self) -> SecretStr | None:
        """Use the bot pairing key for the dashboard unless an override is supplied."""
        return self.admin_api_key or self.telegram_notification_access_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
