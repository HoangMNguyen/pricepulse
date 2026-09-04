"""Bronze layer: verbatim gzip-JSON payloads, keyed raw/<source>/<date>/<time>-<id>.json.gz."""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pricepulse.config import Settings


class RawStore(Protocol):
    def put(self, key: str, payload: dict[str, Any]) -> str: ...

    def get(self, key: str) -> dict[str, Any]: ...


def make_key(source: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"raw/{source}/{now:%Y-%m-%d}/{now:%H%M%S}-{uuid.uuid4().hex[:8]}.json.gz"


def _encode(payload: dict[str, Any]) -> bytes:
    return gzip.compress(json.dumps(payload, separators=(",", ":")).encode())


def _decode(blob: bytes) -> dict[str, Any]:
    return json.loads(gzip.decompress(blob))


class LocalRawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, key: str, payload: dict[str, Any]) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_encode(payload))
        return key

    def get(self, key: str) -> dict[str, Any]:
        return _decode((self.root / key).read_bytes())


class S3RawStore:
    def __init__(self, bucket: str) -> None:
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client("s3")

    def put(self, key: str, payload: dict[str, Any]) -> str:
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=_encode(payload),
            ContentType="application/json",
            ContentEncoding="gzip",
        )
        return key

    def get(self, key: str) -> dict[str, Any]:
        return _decode(self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())


def make_raw_store(settings: Settings) -> RawStore:
    if settings.raw_bucket:
        return S3RawStore(settings.raw_bucket)
    if settings.raw_local_dir:
        return LocalRawStore(settings.raw_local_dir)
    raise RuntimeError("set RAW_BUCKET (AWS) or RAW_LOCAL_DIR (local)")
