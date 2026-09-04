"""Lambda Destination target: receives the processor's return value under `responsePayload`."""

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.services.digest import build_digests
from pricepulse.services.ingest import ProcessResult
from pricepulse.services.mail import send_digests

logger = Logger()


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
    logger.info("cdn invalidated", distribution_id=distribution_id)


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, _context: object) -> dict:
    settings = get_settings()
    payload = event.get("responsePayload", event)
    result = ProcessResult.from_dict(payload)
    if not result.skipped and settings.cloudfront_distribution_id:
        invalidate_cdn(settings.cloudfront_distribution_id, result.raw_object_key)
    if result.skipped or not result.alerts:
        logger.info("nothing to send", key=result.raw_object_key, skipped=result.skipped)
        return {"sent": 0}
    digests = build_digests(result, settings.alert_recipients, settings.public_base_url)
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    sent = send_digests(digests, settings.ses_sender)
    logger.info("digests sent", sent=sent, alerts=len(result.alerts), key=result.raw_object_key)
    return {"sent": sent}
