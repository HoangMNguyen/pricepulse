"""Lambda Destination target: receives the processor's return value under `responsePayload`."""

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.services.digest import build_digests
from pricepulse.services.ingest import ProcessResult
from pricepulse.services.mail import send_digests

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, _context: object) -> dict:
    settings = get_settings()
    payload = event.get("responsePayload", event)
    result = ProcessResult.from_dict(payload)
    if result.skipped or not result.alerts:
        logger.info("nothing to send", key=result.raw_object_key, skipped=result.skipped)
        return {"sent": 0}
    digests = build_digests(result, settings.alert_recipients)
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    sent = send_digests(digests, settings.ses_sender)
    logger.info("digests sent", sent=sent, alerts=len(result.alerts), key=result.raw_object_key)
    return {"sent": sent}
