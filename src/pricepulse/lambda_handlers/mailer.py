"""S3 ObjectCreated target for outbox/ keys: render the transactional email and send it via SES."""

from urllib.parse import unquote_plus

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.services.mail import render_confirm, send_email
from pricepulse.storage.outbox import make_outbox

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, _context: object) -> dict:
    settings = get_settings()
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    outbox = make_outbox(settings)
    sent = 0
    for record in event.get("Records", []):
        key = unquote_plus(record["s3"]["object"]["key"])
        message = outbox.get(key)
        subject, html, text, unsubscribe = render_confirm(message, settings.public_base_url)
        send_email(message["email"], subject, html, text, settings.ses_sender, unsubscribe)
        sent += 1
        logger.info("sent", kind=message["kind"], key=key)
    return {"sent": sent}
