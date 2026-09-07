"""Delivery markers for digests: one empty object per (run, recipient) under sent/.

`notify` has no database by design (internet-facing, SES only), so the durable record of
"this recipient already got this run's digest" lives next to the raw payloads. The marker is
written after SES accepts the mail: a worker killed between the two resends that one recipient
on retry (at-least-once, duplicates bounded to the in-flight recipient), while every recipient
already marked is skipped.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from pricepulse.config import Settings


class DeliveryLog(Protocol):
    def was_sent(self, run_key: str, recipient: str) -> bool: ...

    def mark_sent(self, run_key: str, recipient: str) -> None: ...


def marker_key(run_key: str, recipient: str) -> str:
    """sent/<raw key without raw/ and the .json.gz suffix>/<sha256(email)[:24]>; addresses
    never appear in object names."""
    run = run_key.removeprefix("raw/").removesuffix(".json.gz")
    digest = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()[:24]
    return f"sent/{run}/{digest}"


class LocalDeliveryLog:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def was_sent(self, run_key: str, recipient: str) -> bool:
        return (self.root / marker_key(run_key, recipient)).exists()

    def mark_sent(self, run_key: str, recipient: str) -> None:
        path = self.root / marker_key(run_key, recipient)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


class S3DeliveryLog:
    def __init__(self, bucket: str) -> None:
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client("s3")

    def was_sent(self, run_key: str, recipient: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=marker_key(run_key, recipient))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def mark_sent(self, run_key: str, recipient: str) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=marker_key(run_key, recipient), Body=b"")


def make_delivery_log(settings: Settings) -> DeliveryLog:
    if settings.raw_bucket:
        return S3DeliveryLog(settings.raw_bucket)
    if settings.raw_local_dir:
        return LocalDeliveryLog(settings.raw_local_dir)
    raise RuntimeError("set RAW_BUCKET (AWS) or RAW_LOCAL_DIR (local)")
