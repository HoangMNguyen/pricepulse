"""Runtime configuration loaded from environment variables (and `.env` locally)."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pricepulse_env: Literal["local", "test", "dev"] = "local"
    user_agent: str = "pricepulse/0.1"
    aws_region: str = Field(default="us-east-1", validation_alias="AWS_REGION")

    # Database: a full SQLAlchemy URL (local), or the name of an SSM SecureString parameter
    # holding one (AWS). Read once per cold start.
    database_url: str | None = None
    database_url_ssm: str | None = None
    db_connect_wait_s: int = 45

    # Raw payload storage: local directory or S3 bucket.
    raw_local_dir: str | None = None
    raw_bucket: str | None = None

    # Alerts.
    alert_recipients: Annotated[list[str], NoDecode] = Field(default_factory=list)
    alert_min_discount_pct: Decimal = Decimal("20")
    retention_months: int = 13
    ses_sender: str | None = None

    # API.
    api_key: str = "dev-key"
    public_base_url: str = "http://localhost:8000"  # links in emails
    cloudfront_distribution_id: str | None = None  # set on AWS: notify invalidates after each run

    @field_validator("alert_recipients", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
