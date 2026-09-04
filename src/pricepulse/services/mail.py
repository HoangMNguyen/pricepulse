"""Render emails with Jinja2 and deliver them through SES v2 (one email per recipient)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pricepulse.config import get_settings
from pricepulse.services.digest import Digest

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


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str,
    sender: str,
    list_unsubscribe: str | None = None,
) -> None:
    import boto3

    ses = boto3.client("sesv2", region_name=get_settings().aws_region)
    simple: dict[str, Any] = {
        "Subject": {"Data": subject, "Charset": "UTF-8"},
        "Body": {
            "Html": {"Data": html, "Charset": "UTF-8"},
            "Text": {"Data": text, "Charset": "UTF-8"},
        },
    }
    if list_unsubscribe:
        simple["Headers"] = [{"Name": "List-Unsubscribe", "Value": f"<{list_unsubscribe}>"}]
    ses.send_email(
        FromEmailAddress=sender, Destination={"ToAddresses": [to]}, Content={"Simple": simple}
    )


def send_digests(digests: dict[str, Digest], sender: str) -> int:
    sent = 0
    for to, digest in digests.items():
        unsubscribe = None
        if digest.watcher_only:
            unsubscribe = unsubscribe_url(digest.base_url, digest.watch_hits[0].watch_tokens[to])
        send_email(
            to, digest.subject, render_html(digest), render_text(digest), sender, unsubscribe
        )
        sent += 1
        log.info("sent digest to %s: %s", to, digest.subject)
    return sent
