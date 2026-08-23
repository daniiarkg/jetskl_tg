from pathlib import Path

from sqlalchemy import func, select

from leadfinder.config import Settings
from leadfinder.db import Database
from leadfinder.models import DiscoveryQuery
from leadfinder.profiles import JETSKI_MIAMI
from leadfinder.repository import upsert_profile


def test_profile_seed_is_idempotent(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    database = Database(settings)
    database.create_all()

    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, query_limit=20)
    with database.session() as session:
        upsert_profile(session, JETSKI_MIAMI, query_limit=20)
    with database.session() as session:
        count = session.scalar(select(func.count()).select_from(DiscoveryQuery))

    assert count == 20
