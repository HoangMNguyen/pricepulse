"""Post-processing side effects of a run: CDN invalidation and the alert digests."""

from __future__ import annotations

import logging

from pricepulse.config import Settings
from pricepulse.services.digest import build_digests
from pricepulse.services.ingest import ProcessResult
from pricepulse.services.mail import send_digests, ses_client
from pricepulse.storage.delivery import make_delivery_log

log = logging.getLogger(__name__)


def invalidate_cdn(distribution_id: str, caller_reference: str) -> None:
    """Fresh data landed: drop every cached page. Two calls/day, inside the free 1,000 paths."""
    import boto3

    boto3.client("cloudfront").create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": caller_reference,
        },
    )
    log.info("cdn invalidated: %s", distribution_id)


def report_failures(topic_arn: str, key: str, failed: dict[str, str]) -> None:
    """One SNS message per run listing the recipients whose digest SES rejected. The operator
    re-sends by hand (`pricepulse notify --key-json`); nothing is retried automatically."""
    import boto3

    lines = "\n".join(f"{to}: {err}" for to, err in sorted(failed.items()))
    boto3.client("sns").publish(
        TopicArn=topic_arn,
        Subject=f"pricepulse: {len(failed)} digest(s) not delivered"[:100],
        Message=f"run {key}\n{lines}",
    )


def run_notify(result: ProcessResult, settings: Settings) -> int:
    """Returns the number of digests sent. Delivery is at-most-once per recipient: a rejected
    send is reported to the alarms topic, never retried by re-invoking this function."""
    if not result.skipped and settings.cloudfront_distribution_id:
        invalidate_cdn(settings.cloudfront_distribution_id, result.raw_object_key)
    if result.skipped or not result.alerts:
        return 0
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    digests = build_digests(result, settings.alert_recipients, settings.public_base_url)
    sent, failed = send_digests(
        digests,
        settings.ses_sender,
        ses_client(settings),
        make_delivery_log(settings),
        result.raw_object_key,
    )
    if failed:
        if not settings.alarms_topic_arn:
            raise RuntimeError(f"{len(failed)} digest(s) failed: {failed}")
        report_failures(settings.alarms_topic_arn, result.raw_object_key, failed)
    return sent
