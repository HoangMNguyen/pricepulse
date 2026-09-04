"""SQLAlchemy engine factory.

Locally: `DATABASE_URL` with a pooled engine. On AWS: the URL (with credentials) lives in an SSM
SecureString parameter named by `DATABASE_URL_SSM`, fetched once per cold start; `NullPool`
because a Lambda invocation holds at most one connection.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_exponential

from pricepulse.config import Settings, get_settings

_engine: Engine | None = None


def resolve_database_url(settings: Settings) -> str:
    if settings.database_url:
        return settings.database_url
    if settings.database_url_ssm:
        from aws_lambda_powertools.utilities import parameters

        return parameters.get_parameter(settings.database_url_ssm, decrypt=True, max_age=300)
    raise RuntimeError("DATABASE_URL or DATABASE_URL_SSM is required")


def make_engine(settings: Settings) -> Engine:
    url = resolve_database_url(settings)
    if settings.pricepulse_env == "dev":
        return create_engine(url, pool_pre_ping=True, poolclass=NullPool)
    return create_engine(url, pool_pre_ping=True)


def wait_for_db(engine: Engine, max_wait_s: int) -> None:
    """Open and close one connection, retrying while a suspended database resumes."""
    for attempt in Retrying(
        stop=stop_after_delay(max_wait_s),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type(OperationalError),
        reraise=True,
    ):
        with attempt, engine.connect():
            pass


def get_engine() -> Engine:
    """Process-wide singleton so warm Lambda containers reuse the engine."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        settings = get_settings()
        _engine = make_engine(settings)
        wait_for_db(_engine, settings.db_connect_wait_s)
    return _engine
