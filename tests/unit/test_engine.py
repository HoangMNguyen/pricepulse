import boto3
import pytest
from moto import mock_aws
from sqlalchemy.pool import NullPool

from pricepulse.config import Settings
from pricepulse.db.engine import make_engine, resolve_database_url

LOCAL = "postgresql+psycopg://u:p@localhost:5433/db"


def test_direct_url_wins() -> None:
    s = Settings(database_url=LOCAL, database_url_ssm="/x", _env_file=None)
    assert resolve_database_url(s) == LOCAL


@mock_aws
def test_url_from_ssm_secure_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name="us-east-1")
    ssm.put_parameter(Name="/pricepulse/test/database_url/app_rw", Type="SecureString", Value=LOCAL)
    s = Settings(
        pricepulse_env="dev",
        database_url=None,
        database_url_ssm="/pricepulse/test/database_url/app_rw",
        _env_file=None,
    )
    assert resolve_database_url(s) == LOCAL
    engine = make_engine(s)
    assert isinstance(engine.pool, NullPool)


def test_missing_url_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="DATABASE_URL or DATABASE_URL_SSM"):
        resolve_database_url(Settings(database_url=None, _env_file=None))
