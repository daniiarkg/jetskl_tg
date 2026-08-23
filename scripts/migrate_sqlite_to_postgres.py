from __future__ import annotations

import argparse
from collections.abc import Sequence

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, func, select, text

from leadfinder.db import _sqlalchemy_database_url
from leadfinder.models import Base


class DeploymentDatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str


def migrate_data(source_url: str, target_url: str) -> dict[str, int]:
    source = create_engine(_sqlalchemy_database_url(source_url))
    target = create_engine(_sqlalchemy_database_url(target_url), pool_pre_ping=True)
    Base.metadata.create_all(target)

    copied: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        populated = {
            table.name: int(
                target_connection.scalar(select(func.count()).select_from(table)) or 0
            )
            for table in Base.metadata.sorted_tables
        }
        nonempty = {name: count for name, count in populated.items() if count}
        if nonempty:
            details = ", ".join(f"{name}={count}" for name, count in nonempty.items())
            raise RuntimeError(f"Target database is not empty: {details}")

        for table in Base.metadata.sorted_tables:
            rows = [dict(row._mapping) for row in source_connection.execute(select(table))]
            if rows:
                target_connection.execute(table.insert(), rows)
            copied[table.name] = len(rows)

        if target.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                integer_primary_keys = [
                    column
                    for column in table.primary_key.columns
                    if column.autoincrement is True or column.autoincrement == "auto"
                ]
                for column in integer_primary_keys:
                    maximum = target_connection.scalar(select(func.max(column)))
                    if maximum is None:
                        continue
                    sequence_name = target_connection.scalar(
                        select(
                            func.pg_get_serial_sequence(table.name, column.name)
                        )
                    )
                    if sequence_name:
                        target_connection.execute(
                            text(
                                "SELECT setval(CAST(:sequence_name AS regclass), "
                                ":maximum, true)"
                            ),
                            {
                                "sequence_name": sequence_name,
                                "maximum": int(maximum),
                            },
                        )

    source.dispose()
    target.dispose()
    return copied


def _summary(copied: dict[str, int]) -> str:
    populated = [f"{name}={count}" for name, count in copied.items() if count]
    return "Migrated " + (", ".join(populated) if populated else "an empty database")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time copy from the local Leadfinder SQLite database to Neon"
    )
    parser.add_argument(
        "--source",
        default="sqlite:///data/leadfinder.db",
        help="Source SQLAlchemy URL (defaults to the local Leadfinder SQLite database)",
    )
    args = parser.parse_args(argv)
    target_url = DeploymentDatabaseSettings().database_url
    print(_summary(migrate_data(args.source, target_url)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
