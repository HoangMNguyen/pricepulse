"""The production DB-role contract: app_rw (DML only) must be able to run a full ingestion,
including partition creation and the materialized-view refresh, via the SECURITY DEFINER helpers.

Production order is mirrored: scripts/bootstrap_db.sh (role + default privileges) runs before
`alembic upgrade head`, so the EXECUTE / matview SELECT grants must come from the migrations'
conditional GRANT blocks — nothing here grants them. The migrator is the test DB owner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from pricepulse.config import Settings
from pricepulse.services.ingest import run_process
from pricepulse.storage.raw import LocalRawStore

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ROOT = FIXTURES.parents[1]

# Exactly what scripts/bootstrap_db.sh does (the default privileges there are FOR ROLE
# app_migrator; here the migrator is the connected user, so no FOR ROLE).
BOOTSTRAP_SQL = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "GRANT CONNECT ON DATABASE {db} TO app_rw",
    "GRANT USAGE ON SCHEMA public TO app_rw",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_rw",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_rw",
]


@pytest.fixture(scope="module")
def _bootstrapped_schema(db_engine: Engine, test_db_url: str) -> None:
    """Role + bootstrap grants first, then a fresh migration run on top (conftest's db_engine
    migrated before the role existed, so its conditional GRANTs were skipped)."""
    db = make_url(test_db_url).database
    with db_engine.begin() as c:
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'app_rw'")).scalar()
        if not exists:
            c.execute(text("CREATE ROLE app_rw LOGIN PASSWORD 'app_rw'"))
        for stmt in BOOTSTRAP_SQL:
            c.execute(text(stmt.format(db=f'"{db}"')))
    cfg = Config(str(ROOT / "alembic.ini"))
    with db_engine.begin() as c:
        cfg.attributes["connection"] = c
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")


@pytest.fixture
def app_rw_engine(_bootstrapped_schema: None, conn: Engine, test_db_url: str) -> Engine:
    url = make_url(test_db_url).set(username="app_rw", password="app_rw")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_app_rw_cannot_ddl_directly(app_rw_engine: Engine) -> None:
    with app_rw_engine.connect() as c:
        assert c.execute(text("SELECT current_user")).scalar() == "app_rw"
        with pytest.raises(Exception, match="permission denied|must be owner"):
            c.execute(text("REFRESH MATERIALIZED VIEW product_price_summary"))
    with (
        app_rw_engine.connect() as c,
        pytest.raises(Exception, match="permission denied|must be owner"),
    ):
        c.execute(
            text(
                "CREATE TABLE price_observation_1999_01 PARTITION OF price_observation "
                "FOR VALUES FROM ('1999-01-01') TO ('1999-02-01')"
            )
        )


def test_full_ingestion_as_app_rw(app_rw_engine: Engine, settings: Settings) -> None:
    raw = {
        "source": "ikea",
        "fetched_at": "2027-03-15T13:00:00+00:00",  # a month with no pre-created partition
        "requests": [
            {
                "url": "offers",
                "status": 200,
                "body": json.loads((FIXTURES / "ikea_offers.json").read_text()),
            }
        ],
    }
    key = LocalRawStore(settings.raw_local_dir).put("raw/ikea/2027-03-15/r.json.gz", raw)
    result = run_process(key, settings, app_rw_engine)
    assert result.products_seen == 5 and result.observations_inserted == 5
    with app_rw_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM product_price_summary")).scalar() == 5
        assert c.execute(
            text("SELECT to_regclass('price_observation_2027_03') IS NOT NULL")
        ).scalar()
