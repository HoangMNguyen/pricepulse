import os

import pytest

from pricepulse.lambda_handlers import process
from pricepulse.services.ingest import ProcessResult


class _Ctx:
    function_name = "process"
    memory_limit_in_mb = 1024
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:process"
    aws_request_id = "req-1"


@pytest.fixture(autouse=True)
def _metrics_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(os.environ, "POWERTOOLS_METRICS_NAMESPACE", "PricePulse")
    monkeypatch.setitem(os.environ, "POWERTOOLS_SERVICE_NAME", "pricepulse")


def _emitted(capsys: pytest.CaptureFixture[str]) -> dict:
    import json

    lines = [ln for ln in capsys.readouterr().out.splitlines() if '"_aws"' in ln]
    return json.loads(lines[-1]) if lines else {}


def test_skipped_run_emits_no_products_seen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    skipped = ProcessResult(None, "ikea", "raw/ikea/x.json.gz", 0, 0, [], skipped=True)
    monkeypatch.setattr(process, "run_process", lambda key: skipped)
    out = process.handler({"raw_object_key": "raw/ikea/x.json.gz"}, _Ctx())
    assert out["skipped"] is True
    assert "ProductsSeen" not in _emitted(capsys)


def test_real_run_emits_products_seen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    done = ProcessResult(1, "ikea", "raw/ikea/x.json.gz", 332, 332, [])
    monkeypatch.setattr(process, "run_process", lambda key: done)
    process.handler({"Records": [{"s3": {"object": {"key": "raw/ikea/x.json.gz"}}}]}, _Ctx())
    blob = _emitted(capsys)
    assert blob["ProductsSeen"] == [332.0] and blob["AlertsRaised"] == [0.0]
    assert blob["source"] == "ikea"
