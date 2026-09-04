"""S3 ObjectCreated target. Processes every key in the event; the return value becomes the
notify Lambda's input via Lambda Destinations (on_success)."""

from urllib.parse import unquote_plus

from aws_lambda_powertools import Logger, Metrics
from aws_lambda_powertools.metrics import MetricUnit

from pricepulse.services.ingest import run_process

logger = Logger()
metrics = Metrics(namespace="PricePulse")


@logger.inject_lambda_context(log_event=False)
@metrics.log_metrics(capture_cold_start_metric=True)
def handler(event: dict, _context: object) -> dict:
    keys = [unquote_plus(r["s3"]["object"]["key"]) for r in event.get("Records", [])]
    if "raw_object_key" in event:  # manual re-run: {"raw_object_key": "raw/..."}
        keys.append(event["raw_object_key"])
    if len(keys) != 1:
        raise ValueError(f"expected exactly one key per invocation, got {keys}")
    result = run_process(keys[0])
    metrics.add_dimension("source", result.source)
    metrics.add_metric("ProductsSeen", MetricUnit.Count, result.products_seen)
    metrics.add_metric("AlertsRaised", MetricUnit.Count, len(result.alerts))
    logger.info("processed", **{k: v for k, v in result.to_dict().items() if k != "alerts"})
    return result.to_dict()
