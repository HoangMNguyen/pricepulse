"""Daily EventBridge target: dump every watch to backup/watch/<date>/<time>.json.gz."""

from aws_lambda_powertools import Logger

from pricepulse.config import get_settings
from pricepulse.db.engine import get_engine
from pricepulse.services.backup import backup_watches

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(_event: dict, _context: object) -> dict:
    key, watches = backup_watches(get_settings(), get_engine())
    logger.info("watches backed up", key=key, watches=watches)
    return {"key": key, "watches": watches}
