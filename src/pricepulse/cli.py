"""Developer CLI: the same service functions the Lambdas call, runnable locally."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(message)s")


@app.command()
def scrape(source: str = typer.Option(..., help="ikea | uniqlo")) -> None:
    """Fetch a retailer and store the raw payload; prints the raw object key."""
    from pricepulse.services.ingest import run_scrape

    typer.echo(run_scrape(source))


@app.command()
def process(key: str = typer.Option(..., help="raw object key from `scrape`")) -> None:
    """Load a raw payload into Postgres, compute alerts; prints the ProcessResult as JSON."""
    from pricepulse.services.ingest import run_process

    typer.echo(json.dumps(run_process(key).to_dict(), indent=2))


@app.command()
def run(source: str = typer.Option(..., help="ikea | uniqlo")) -> None:
    """scrape + process in one go."""
    from pricepulse.services.ingest import run_process, run_scrape

    typer.echo(json.dumps(run_process(run_scrape(source)).to_dict(), indent=2))


@app.command()
def notify(
    key_json: Path = typer.Option(..., exists=True, help="file with a ProcessResult JSON"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print digests instead of sending"),
) -> None:
    """Build per-recipient digests from a ProcessResult and send them via SES."""
    from pricepulse.config import get_settings
    from pricepulse.services.digest import build_digests
    from pricepulse.services.ingest import ProcessResult
    from pricepulse.services.mail import render_text, send_digests

    settings = get_settings()
    result = ProcessResult.from_dict(json.loads(key_json.read_text()))
    digests = build_digests(result, settings.alert_recipients, settings.public_base_url)
    if dry_run:
        for to, digest in digests.items():
            typer.echo(f"--- to: {to}\n{render_text(digest)}")
        typer.echo(f"{len(digests)} digest(s) (dry run)")
        return
    if not settings.ses_sender:
        raise typer.BadParameter("SES_SENDER is not set")
    typer.echo(f"sent {send_digests(digests, settings.ses_sender)} email(s)")


@app.command()
def mailer(
    key: str = typer.Option(..., help="outbox key, e.g. outbox/watch_confirm/2026-09-04/<id>.json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print the email instead of sending"),
) -> None:
    """Send one transactional email from the outbox (what the mailer Lambda does per S3 event)."""
    from pricepulse.config import get_settings
    from pricepulse.services.mail import render_confirm, send_email
    from pricepulse.storage.outbox import make_outbox

    settings = get_settings()
    message = make_outbox(settings).get(key)
    subject, html, text, unsubscribe = render_confirm(message, settings.public_base_url)
    if dry_run:
        typer.echo(f"--- to: {message['email']}\nsubject: {subject}\n{text}")
        return
    if not settings.ses_sender:
        raise typer.BadParameter("SES_SENDER is not set")
    send_email(message["email"], subject, html, text, settings.ses_sender, unsubscribe)
    typer.echo(f"sent to {message['email']}")


@app.command()
def migrate() -> None:
    """alembic upgrade head."""
    from pricepulse.services.migrate import upgrade_head

    upgrade_head()


@app.command()
def serve(port: int = 8000) -> None:
    """Run the API + dashboard with uvicorn (dev only)."""
    import uvicorn

    uvicorn.run("pricepulse.api.app:create_app", factory=True, reload=True, port=port)


if __name__ == "__main__":
    app()
