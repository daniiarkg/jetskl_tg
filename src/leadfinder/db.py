from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from leadfinder.config import Settings
from leadfinder.models import Base


def _sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _prepare_sqlite_directory(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url.removeprefix(prefix)
    if raw_path == ":memory:":
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


class Database:
    def __init__(self, settings: Settings):
        _prepare_sqlite_directory(settings.database_url)
        database_url = _sqlalchemy_database_url(settings.database_url)
        connect_args = (
            {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        )
        self.engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args=connect_args,
        )
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self._session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._run_additive_migrations()

    def _run_additive_migrations(self) -> None:
        """Keep existing local v1 databases usable without a destructive migration."""
        timestamp_type = (
            "DATETIME"
            if self.engine.dialect.name == "sqlite"
            else "TIMESTAMP WITH TIME ZONE"
        )
        additions = {
            "chat_sources": {
                "platform": "VARCHAR(30) NOT NULL DEFAULT 'telegram'",
                "external_source_id": "VARCHAR(500)",
                "source_url": "VARCHAR(2000)",
                "participant_count_updated_at": timestamp_type,
            },
            "signals": {
                "external_message_id": "VARCHAR(500)",
                "author_external_id": "VARCHAR(500)",
            },
            "leads": {
                "platform": "VARCHAR(30) NOT NULL DEFAULT 'telegram'",
                "external_user_id": "VARCHAR(500)",
                "language": "VARCHAR(20)",
            },
            "workflow_states": {
                "generation": "VARCHAR(64)",
            },
        }
        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())
        with self.engine.begin() as connection:
            for table_name, columns in additions.items():
                if table_name not in existing_tables:
                    continue
                existing_columns = {
                    column["name"] for column in inspector.get_columns(table_name)
                }
                for column_name, definition in columns.items():
                    if column_name in existing_columns:
                        continue
                    connection.execute(
                        text(
                            f'ALTER TABLE "{table_name}" ADD COLUMN '
                            f'"{column_name}" {definition}'
                        )
                    )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
