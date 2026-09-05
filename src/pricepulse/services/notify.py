"""Post-processing side effects of a run: CDN invalidation and the alert digests."""

from __future__ import annotations

import logging

from pricepulse.config import Settings
from pricepulse.services.digest import build_digests
from pricepulse.services.ingest import ProcessResult
from pricepulse.services.mail import send_digests, ses_client

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


def run_notify(result: ProcessResult, settings: Settings) -> int:
    """Returns the number of digests sent."""
    if not result.skipped and settings.cloudfront_distribution_id:
        invalidate_cdn(settings.cloudfront_distribution_id, result.raw_object_key)
    if result.skipped or not result.alerts:
        return 0
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    digests = build_digests(result, settings.alert_recipients, settings.public_base_url)
    return send_digests(digests, settings.ses_sender, ses_client(settings))
