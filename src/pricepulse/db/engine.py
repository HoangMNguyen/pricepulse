"""SQLAlchemy engine factory.

Locally: plain URL with a pooled engine. On AWS: no stored password — every connection
signs a short-lived IAM auth token via boto3 in the `do_connect` hook, uses `verify-full`
TLS against the bundled RDS CA, and `NullPool` (one connection per Lambda invocation).
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_exponential

from pricepulse.config import Settings, get_settings

_engine: Engine | None = None


def rds_ca_bundle_path() -> str:
    return str(files("pricepulse.certs").joinpath("global-bundle.pem"))


def make_engine(settings: Settings) -> Engine:
    if not settings.db_iam_auth:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required when DB_IAM_AUTH is false")
        return create_engine(settings.database_url, pool_pre_ping=True)

    if not settings.db_host:
        raise RuntimeError("DB_HOST is required when DB_IAM_AUTH is true")
    url = (
        f"postgresql+psycopg://{settings.db_user}@{settings.db_host}:{settings.db_port}"
        f"/{settings.db_name}"
    )
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"sslmode": "verify-full", "sslrootcert": rds_ca_bundle_path()},
    )

    @event.listens_for(engine, "do_connect")
    def _inject_iam_token(dialect: Any, conn_rec: Any, cargs: Any, cparams: dict) -> None:
        import boto3

        client = boto3.client("rds", region_name=settings.aws_region)
        cparams["password"] = client.generate_db_auth_token(
            DBHostname=settings.db_host,
            Port=settings.db_port,
            DBUsername=settings.db_user,
            Region=settings.aws_region,
        )

    return engine


@retry(
    stop=stop_after_delay(45),
    wait=wait_exponential(multiplier=1, max=8),
    retry=retry_if_exception_type(OperationalError),
    reraise=True,
)
def wait_for_db(engine: Engine) -> None:
    """Open and close one connection, retrying while Aurora resumes from 0 ACU (~15 s)."""
    with engine.connect():
        pass


def get_engine() -> Engine:
    """Process-wide singleton so warm Lambda containers reuse the engine."""
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = make_engine(get_settings())
        wait_for_db(_engine)
    return _engine
