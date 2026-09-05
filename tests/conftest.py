"""Shared fixtures. Integration tests need Postgres (default: compose service on :5433)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from pricepulse.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_URL = "postgresql+psycopg://pricepulse:pricepulse@localhost:5433/pricepulse_test"


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def test_db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)


@pytest.fixture(scope="session")
def db_engine(test_db_url: str) -> Iterator[Engine]:
    db = make_url(test_db_url).database
    if not db or not db.endswith("_test"):
        pytest.exit(
            f"refusing to reset database {db!r}: TEST_DATABASE_URL must name a *_test database"
        )
    _ensure_database(test_db_url)
    engine = create_engine(test_db_url)
    cfg = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Settings are cached per process; env-driven tests must not leak into each other."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(test_db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointing at the test DB and a temp raw dir; also wired into get_settings()."""
    s = Settings(
        pricepulse_env="test",
        database_url=test_db_url,
        raw_local_dir=str(tmp_path / "raw"),
        alert_recipients=["owner@example.com"],
        api_key="test-key",
        _env_file=None,
    )
    monkeypatch.setattr("pricepulse.config.get_settings", lambda: s)
    return s


@pytest.fixture
def conn(db_engine: Engine, settings: Settings) -> Iterator[Engine]:
    """Clean tables before each integration test; hands back the engine."""
    with db_engine.begin() as c:
        c.execute(text("TRUNCATE product, ingestion_run, watch, alert RESTART IDENTITY CASCADE"))
        c.execute(text("REFRESH MATERIALIZED VIEW product_price_summary"))
    yield db_engine
