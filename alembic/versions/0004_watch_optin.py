"""Watch double opt-in: confirmation token and timestamps.

Revision ID: 0004_watch_optin
Revises: 0003_summary_v2
"""

from alembic import op

revision = "0004_watch_optin"
down_revision = "0003_summary_v2"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    ALTER TABLE watch
      ADD COLUMN token TEXT,
      ADD COLUMN confirmed_at TIMESTAMPTZ,
      ADD COLUMN confirmation_sent_at TIMESTAMPTZ
    """,
    # Pre-existing rows were owner-created through the keyed API: treat them as confirmed.
    """
    UPDATE watch
       SET token = replace(gen_random_uuid()::text, '-', ''), confirmed_at = created_at
     WHERE token IS NULL
    """,
    "ALTER TABLE watch ALTER COLUMN token SET NOT NULL",
    "CREATE UNIQUE INDEX watch_token_idx ON watch (token)",
    "CREATE INDEX watch_email_unconfirmed_idx ON watch (email) WHERE confirmed_at IS NULL",
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS watch_email_unconfirmed_idx",
    "DROP INDEX IF EXISTS watch_token_idx",
    "ALTER TABLE watch DROP COLUMN token, DROP COLUMN confirmed_at, DROP COLUMN confirmation_sent_at",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
