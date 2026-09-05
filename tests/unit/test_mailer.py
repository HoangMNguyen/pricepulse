import json

import boto3
import pytest
from moto import mock_aws

from pricepulse.lambda_handlers import mailer
from pricepulse.storage.outbox import LocalOutbox, S3Outbox


class _Ctx:
    function_name = "mailer"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:mailer"
    aws_request_id = "req-1"


MESSAGE = {
    "kind": "watch_confirm",
    "email": "w@example.com",
    "product_id": 7,
    "product_name": "KALLAX shelf",
    "product_url": "https://ikea.example/kallax",
    "min_discount_pct": "10.0",
    "token": "tok-123",
}


def test_local_outbox_round_trip(tmp_path) -> None:  # noqa: ANN001
    box = LocalOutbox(tmp_path)
    key = box.put(MESSAGE)
    assert key.startswith("outbox/watch_confirm/") and key.endswith(".json")
    assert box.get(key) == MESSAGE
    assert json.loads((tmp_path / key).read_text())["token"] == "tok-123"


@mock_aws
def test_mailer_handler_sends_one_email_per_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SES_SENDER", "sender@example.com")
    monkeypatch.setenv("RAW_BUCKET", "pricepulse-test-raw")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://pp.example")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="pricepulse-test-raw")
    boto3.client("sesv2", region_name="us-east-1").create_email_identity(
        EmailIdentity="sender@example.com"
    )
    key = S3Outbox("pricepulse-test-raw").put(MESSAGE)
    event = {"Records": [{"s3": {"object": {"key": key}}}]}
    assert mailer.handler(event, _Ctx()) == {"sent": 1}
    assert mailer.handler({"Records": []}, _Ctx()) == {"sent": 0}
