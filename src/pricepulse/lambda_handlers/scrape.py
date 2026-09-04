"""EventBridge Scheduler target. Event: {"source": "ikea" | "uniqlo"}."""

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit

from pricepulse.services.ingest import run_scrape

logger = Logger()
metrics = Metrics(namespace="PricePulse")


@logger.inject_lambda_context(log_event=True)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, _context: object) -> dict:
    source = event["source"]
    key = run_scrape(source)
    metrics.add_dimension("source", source)
    metrics.add_metric("ScrapeRuns", MetricUnit.Count, 1)
    logger.info("scraped", source=source, key=key)
    return {"source": source, "raw_object_key": key}
