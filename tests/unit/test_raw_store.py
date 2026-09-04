import re
from datetime import UTC, datetime
from pathlib import Path

import boto3
from moto import mock_aws

from pricepulse.storage.raw import LocalRawStore, S3RawStore, make_key


def test_make_key_format() -> None:
    key = make_key("ikea", datetime(2026, 9, 4, 13, 0, 5, tzinfo=UTC))
    assert re.fullmatch(r"raw/ikea/2026-09-04/130005-[0-9a-f]{8}\.json\.gz", key)


def test_local_store_roundtrip(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path)
    payload = {"source": "ikea", "requests": [{"body": {"a": 1}}]}
    key = store.put("raw/ikea/2026-09-04/x.json.gz", payload)
    assert (tmp_path / key).exists()
    assert store.get(key) == payload


@mock_aws
def test_s3_store_roundtrip() -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="pricepulse-test-raw")
    store = S3RawStore("pricepulse-test-raw")
    key = store.put("raw/uniqlo/2026-09-04/y.json.gz", {"source": "uniqlo", "requests": []})
    s3 = boto3.client("s3", region_name="us-east-1")
    head = s3.head_object(Bucket="pricepulse-test-raw", Key=key)
    assert head["ContentEncoding"] == "gzip"
    assert store.get(key)["source"] == "uniqlo"
