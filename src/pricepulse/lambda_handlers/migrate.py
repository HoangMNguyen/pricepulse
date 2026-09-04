"""Manual / CI-invoked: `alembic upgrade head` as the app_migrator DB user."""

from aws_lambda_powertools import Logger

from pricepulse.services.migrate import upgrade_head

logger = Logger()


@logger.inject_lambda_context(log_event=False)
def handler(_event: dict, _context: object) -> dict:
    upgrade_head()
    logger.info("migrations applied")
    return {"status": "ok"}
