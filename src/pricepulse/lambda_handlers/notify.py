"""Lambda Destination target: receives the processor's return value under `responsePayload`."""

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.services.ingest import ProcessResult
from pricepulse.services.notify import run_notify

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, _context: object) -> dict:
    result = ProcessResult.from_dict(event.get("responsePayload", event))
    sent = run_notify(result, get_settings())
    logger.info(
        "notify done",
        sent=sent,
        alerts=len(result.alerts),
        key=result.raw_object_key,
        skipped=result.skipped,
    )
    return {"sent": sent}
