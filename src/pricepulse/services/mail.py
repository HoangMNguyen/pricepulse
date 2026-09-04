"""Render digests with Jinja2 and deliver them through SES v2 (one email per recipient)."""

from __future__ import annotations

import logging
from pathlib import Path

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


def send_digests(digests: dict[str, Digest], sender: str) -> int:
    if not digests:
        return 0
    import boto3

    ses = boto3.client("sesv2", region_name=get_settings().aws_region)
    sent = 0
    for to, digest in digests.items():
        ses.send_email(
            FromEmailAddress=sender,
            Destination={"ToAddresses": [to]},
            Content={
                "Simple": {
                    "Subject": {"Data": digest.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": render_html(digest), "Charset": "UTF-8"},
                        "Text": {"Data": render_text(digest), "Charset": "UTF-8"},
                    },
                }
            },
        )
        sent += 1
        log.info("sent digest to %s: %s", to, digest.subject)
    return sent
