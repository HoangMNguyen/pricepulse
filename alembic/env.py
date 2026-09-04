"""Alembic environment. Builds the engine through pricepulse.db.engine so IAM auth works in
the migrate Lambda exactly as it does locally. Tests may inject an engine via
`config.attributes["connection"]`."""

from alembic import context
from sqlalchemy import Connection

from pricepulse.config import Settings
from pricepulse.db.engine import make_engine

config = context.config


def _run(connection: Connection) -> None:
    context.configure(connection=connection, version_table_schema="public")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    injected = config.attributes.get("connection")
    if injected is not None:
        _run(injected)
        return
    engine = make_engine(Settings())
    with engine.connect() as connection:
        _run(connection)
        connection.commit()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported; migrations are executed against a live DB")

run_migrations_online()
