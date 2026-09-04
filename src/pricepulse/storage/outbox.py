"""Outbox for transactional mail: one JSON object per message under outbox/<kind>/<date>/<id>.json.

The API writes here (S3 on AWS, a directory locally); the mailer Lambda reacts to the
ObjectCreated event and sends through SES. Keeps SES credentials out of the API function.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pricepulse.config import Settings


class Outbox(Protocol):
    def put(self, message: dict[str, Any]) -> str: ...

    def get(self, key: str) -> dict[str, Any]: ...


def make_key(kind: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"outbox/{kind}/{now:%Y-%m-%d}/{uuid.uuid4().hex}.json"


class LocalOutbox:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, message: dict[str, Any]) -> str:
        key = make_key(message["kind"])
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(message))
        return key

    def get(self, key: str) -> dict[str, Any]:
        return json.loads((self.root / key).read_text())


class S3Outbox:
    def __init__(self, bucket: str) -> None:
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client("s3")

    def put(self, message: dict[str, Any]) -> str:
        key = make_key(message["kind"])
        self._s3.put_object(
            Bucket=self.bucket, Key=key, Body=json.dumps(message), ContentType="application/json"
        )
        return key

    def get(self, key: str) -> dict[str, Any]:
        return json.loads(self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())


def make_outbox(settings: Settings) -> Outbox:
    if settings.raw_bucket:
        return S3Outbox(settings.raw_bucket)
    if settings.raw_local_dir:
        return LocalOutbox(settings.raw_local_dir)
    raise RuntimeError("set RAW_BUCKET (AWS) or RAW_LOCAL_DIR (local)")
