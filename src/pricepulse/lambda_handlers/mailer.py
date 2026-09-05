"""S3 ObjectCreated target for outbox/ keys: render the transactional email and send it via SES."""

from urllib.parse import unquote_plus

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.services.mail import send_outbox_message, ses_client
from pricepulse.storage.outbox import make_outbox

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(event: dict, _context: object) -> dict:
    settings = get_settings()
    outbox = make_outbox(settings)
    ses = ses_client(settings)
    sent = 0
    for record in event.get("Records", []):
        key = unquote_plus(record["s3"]["object"]["key"])
        kind = send_outbox_message(outbox, key, settings, ses)
        sent += 1
        logger.info("sent", kind=kind, key=key)
    return {"sent": sent}
