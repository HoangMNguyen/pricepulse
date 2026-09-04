import os
from decimal import Decimal

import boto3
from moto import mock_aws

from pricepulse.config import get_settings
from pricepulse.lambda_handlers import notify
from pricepulse.services.digest import RETAILER_FLAG_CAP, build_digests
from pricepulse.services.ingest import AlertOut, ProcessResult
from pricepulse.services.mail import render_confirm, render_html, render_text, send_digests

BASE = "https://pp.example"


class _Ctx:
    function_name = "notify"
    memory_limit_in_mb = 256
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:notify"
    aws_request_id = "req-1"


def alert(kind: str, name: str, pct: str, emails: list[str] | None = None) -> AlertOut:
    return AlertOut(
        kind=kind,
        product_id=hash(name) % 1000,
        name=name,
        url=f"https://example.com/{name}",
        source="ikea",
        old_price=Decimal("100.00"),
        new_price=Decimal("100.00") * (1 - Decimal(pct) / 100),
        discount_pct=Decimal(pct),
        emails=emails or [],
        watch_tokens={e: f"tok-{e.split('@')[0]}" for e in (emails or [])},
    )


def result(alerts: list[AlertOut], skipped: bool = False) -> ProcessResult:
    return ProcessResult(1, "ikea", "raw/ikea/x.json.gz", 10, 10, alerts, skipped=skipped)


def test_build_digests_sections_and_recipients() -> None:
    alerts = [
        alert("new_deal", "B", "25.0"),
        alert("new_deal", "A", "40.0"),
        alert("price_drop", "C", "30.0"),
        alert("watch_hit", "C", "30.0", emails=["watcher@example.com", "owner@example.com"]),
        *[alert("retailer_flag", f"F{i:03}", "0.0") for i in range(RETAILER_FLAG_CAP + 5)],
    ]
    digests = build_digests(result(alerts), ["owner@example.com"], BASE)
    assert set(digests) == {"owner@example.com", "watcher@example.com"}
    owner = digests["owner@example.com"]
    assert [a.name for a in owner.new_deals] == ["A", "B"]  # sorted by discount desc
    assert [a.name for a in owner.price_drops] == ["C"]
    assert len(owner.retailer_flags) == RETAILER_FLAG_CAP and owner.retailer_flags_overflow == 5
    assert [a.name for a in owner.watch_hits] == ["C"]
    assert owner.subject.startswith("PricePulse: 2 new deals, 1 price drops (ikea, ")
    watcher = digests["watcher@example.com"]
    assert watcher.new_deals == [] and [a.name for a in watcher.watch_hits] == ["C"]
    assert watcher.watcher_only and not owner.watcher_only
    assert watcher.recipient == "watcher@example.com" and watcher.base_url == BASE
    text = render_text(watcher)
    assert f"{BASE}/watches/unsubscribe/tok-watcher" in text
    assert "You receive this because you confirmed a watch" in text
    assert f"{BASE}/watches/unsubscribe/tok-watcher" in render_html(watcher)
    assert "confirmed a watch" not in render_text(owner)


def test_build_digests_drops_empty_and_skipped() -> None:
    assert build_digests(result([], skipped=True), ["o@example.com"], BASE) == {}
    assert build_digests(result([]), ["o@example.com"], BASE) == {}
    only_watch = [alert("watch_hit", "C", "30.0", emails=["w@example.com"])]
    digests = build_digests(result(only_watch), ["o@example.com"], BASE)
    assert set(digests) == {"w@example.com"}  # owner has nothing -> dropped


def test_render_templates() -> None:
    digest = build_digests(
        result([alert("new_deal", "KALLAX", "40.0"), alert("price_drop", "BILLY", "30.0")]),
        ["o@example.com"],
        BASE,
    )["o@example.com"]
    text = render_text(digest)
    assert "NEW DEALS (1)" in text and "KALLAX" in text and "-40.0%" in text
    assert "PRICE DROPS (1)" in text and "$70.00" in text and "(was $100.00" in text
    html = render_html(digest)
    assert "<s>$100.00</s>" in html and "BILLY" in html and "-30.0%" in html


@mock_aws
def test_send_digests_and_notify_handler() -> None:
    ses = boto3.client("sesv2", region_name="us-east-1")
    ses.create_email_identity(EmailIdentity="sender@example.com")
    alerts = [
        alert("new_deal", "KALLAX", "40.0"),
        alert("watch_hit", "BILLY", "30.0", emails=["w@example.com"]),
    ]
    os.environ.update(
        SES_SENDER="sender@example.com",
        ALERT_RECIPIENTS="o@example.com",
        AWS_REGION="us-east-1",
    )
    get_settings.cache_clear()
    digests = build_digests(result(alerts), ["o@example.com"], BASE)
    assert send_digests(digests, "sender@example.com") == 2

    event = {"responsePayload": result(alerts).to_dict()}
    assert notify.handler(event, _Ctx()) == {"sent": 2}
    skipped = {"responsePayload": result([], skipped=True).to_dict()}
    assert notify.handler(skipped, _Ctx()) == {"sent": 0}
    get_settings.cache_clear()


def test_render_confirm_has_both_links() -> None:
    message = {
        "kind": "watch_confirm",
        "email": "w@example.com",
        "product_id": 7,
        "product_name": "KALLAX shelf",
        "product_url": "https://ikea.example/kallax",
        "min_discount_pct": "10.0",
        "token": "tok-123",
    }
    subject, html, text, unsubscribe = render_confirm(message, BASE)
    assert subject == "Confirm your PricePulse watch: KALLAX shelf"
    assert unsubscribe == f"{BASE}/watches/unsubscribe/tok-123"
    for body in (html, text):
        assert f"{BASE}/watches/confirm/tok-123" in body and unsubscribe in body
        assert "KALLAX shelf" in body and "10.0%" in body


@mock_aws
def test_notify_invalidates_cdn_when_data_landed(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict] = []

    class _CF:
        def create_invalidation(self, **kwargs: object) -> dict:
            calls.append(kwargs)
            return {"Invalidation": {"Id": "I1"}}

    monkeypatch.setattr("boto3.client", lambda name, **_: _CF() if name == "cloudfront" else None)
    os.environ.update(CLOUDFRONT_DISTRIBUTION_ID="E123", AWS_REGION="us-east-1")
    get_settings.cache_clear()
    try:
        # data landed but nothing to send: still invalidates, sends nothing
        assert notify.handler({"responsePayload": result([]).to_dict()}, _Ctx()) == {"sent": 0}
        assert len(calls) == 1
        assert calls[0]["DistributionId"] == "E123"
        assert calls[0]["InvalidationBatch"]["Paths"]["Items"] == ["/*"]
        assert calls[0]["InvalidationBatch"]["CallerReference"] == "raw/ikea/x.json.gz"
        # re-delivered event (skipped) touches nothing
        skipped = {"responsePayload": result([], skipped=True).to_dict()}
        assert notify.handler(skipped, _Ctx()) == {"sent": 0}
        assert len(calls) == 1
    finally:
        os.environ.pop("CLOUDFRONT_DISTRIBUTION_ID", None)
        get_settings.cache_clear()
