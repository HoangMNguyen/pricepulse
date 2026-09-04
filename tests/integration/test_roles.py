"""The production DB-role contract: app_rw (DML only) must be able to run a full ingestion,
including partition creation and the materialized-view refresh, via the SECURITY DEFINER helpers.
Grants mirror scripts/bootstrap_db.sh; the migrator here is the test DB owner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from pricepulse.config import Settings
from pricepulse.services.ingest import run_process
from pricepulse.storage.raw import LocalRawStore

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

BOOTSTRAP_GRANTS = [
    "GRANT USAGE ON SCHEMA public TO app_rw",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_rw",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rw",
    "GRANT EXECUTE ON FUNCTION ensure_price_partition(TIMESTAMPTZ) TO app_rw",
    "GRANT EXECUTE ON FUNCTION refresh_price_summary() TO app_rw",
    "GRANT SELECT ON product_price_summary TO app_rw",
]


@pytest.fixture
def app_rw_engine(conn: Engine, test_db_url: str) -> Engine:
    with conn.begin() as c:
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'app_rw'")).scalar()
        if not exists:
            c.execute(text("CREATE ROLE app_rw LOGIN PASSWORD 'app_rw'"))
        for grant in BOOTSTRAP_GRANTS:
            c.execute(text(grant))
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
    assert result.products_seen == 3 and result.observations_inserted == 3
    with app_rw_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM product_price_summary")).scalar() == 3
        assert c.execute(
            text("SELECT to_regclass('price_observation_2027_03') IS NOT NULL")
        ).scalar()
