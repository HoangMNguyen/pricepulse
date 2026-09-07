"""Render emails with Jinja2 and deliver them through SES v2 (one email per recipient)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pricepulse.config import Settings
from pricepulse.services.digest import Digest
from pricepulse.storage.delivery import DeliveryLog
from pricepulse.storage.outbox import Outbox

log = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parents[1] / "api" / "templates" / "email"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(digest: Digest) -> str:
    return _env.get_template("digest.html").render(d=digest)


def render_text(digest: Digest) -> str:
    return _env.get_template("digest.txt").render(d=digest)


def unsubscribe_url(base_url: str, token: str) -> str:
    return f"{base_url}/watches/unsubscribe/{token}"


def unsubscribe_all_url(base_url: str, token: str) -> str:
    return f"{base_url}/watches/unsubscribe-all/{token}"


def render_confirm(message: dict[str, Any], base_url: str) -> tuple[str, str, str, str]:
    """(subject, html, text, unsubscribe_url) for a `watch_confirm` outbox message."""
    ctx = {
        **message,
        "confirm_url": f"{base_url}/watches/confirm/{message['token']}",
        "unsubscribe_url": unsubscribe_url(base_url, message["token"]),
    }
    subject = f"Confirm your PricePulse watch: {message['product_name']}"
    html = _env.get_template("confirm.html").render(**ctx)
    text = _env.get_template("confirm.txt").render(**ctx)
    return subject, html, text, ctx["unsubscribe_url"]


def ses_client(settings: Settings) -> Any:
    """One SES v2 client per invocation; passed explicitly so tests can substitute a stub."""
    import boto3

    return boto3.client("sesv2", region_name=settings.aws_region)


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str,
    sender: str,
    list_unsubscribe: str | None = None,
    *,
    ses: Any,
) -> None:
    simple: dict[str, Any] = {
        "Subject": {"Data": subject, "Charset": "UTF-8"},
        "Body": {
            "Html": {"Data": html, "Charset": "UTF-8"},
            "Text": {"Data": text, "Charset": "UTF-8"},
        },
    }
    if list_unsubscribe:  # RFC 2369 + RFC 8058 one-click (POST to the same URL)
        simple["Headers"] = [
            {"Name": "List-Unsubscribe", "Value": f"<{list_unsubscribe}>"},
            {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"},
        ]
    ses.send_email(
        FromEmailAddress=sender, Destination={"ToAddresses": [to]}, Content={"Simple": simple}
    )


def send_digests(
    digests: dict[str, Digest], sender: str, ses: Any, delivery: DeliveryLog, run_key: str
) -> tuple[int, dict[str, str]]:
    """Send every digest, one SES call per recipient; returns (sent, {recipient: error}).
    Recipients already marked in `delivery` for this run are skipped, so a re-invocation
    (Lambda retry after a crash, manual re-run) never resends what SES accepted. A failure
    never raises: raising would retry the batch. The caller reports the failures instead."""
    sent = 0
    failed: dict[str, str] = {}
    for to, digest in digests.items():
        if delivery.was_sent(run_key, to):
            log.info("digest to %s already sent for %s", to, run_key)
            continue
        # The header targets every watch of the address (RFC 8058 clients present it as
        # "unsubscribe from this sender"); per-product links in the body stay per-watch.
        unsubscribe = (
            unsubscribe_all_url(digest.base_url, digest.watch_hits[0].watch_tokens[to])
            if digest.watch_hits
            else None
        )
        try:
            send_email(
                to,
                digest.subject,
                render_html(digest),
                render_text(digest),
                sender,
                unsubscribe,
                ses=ses,
            )
        except Exception as exc:  # noqa: BLE001 - one recipient must not block the others
            failed[to] = f"{type(exc).__name__}: {exc}"
            log.exception("digest to %s failed", to)
            continue
        delivery.mark_sent(run_key, to)
        sent += 1
        log.info("sent digest to %s: %s", to, digest.subject)
    return sent, failed


def send_outbox_message(outbox: Outbox, key: str, settings: Settings, ses: Any) -> str:
    """Render and send one transactional outbox message; returns its kind."""
    if not settings.ses_sender:
        raise RuntimeError("SES_SENDER is not configured")
    message = outbox.get(key)
    subject, html, text, unsubscribe = render_confirm(message, settings.public_base_url)
    send_email(message["email"], subject, html, text, settings.ses_sender, unsubscribe, ses=ses)
    return message["kind"]
