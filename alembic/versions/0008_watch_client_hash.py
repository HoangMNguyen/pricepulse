"""`watch.client_hash`: SHA-256 of the requesting client's IP, set on the unconfirmed row so
confirmation mail can be budgeted per client (not only per address, which a script invents
freely). Cleared on confirmation; unconfirmed rows are purged by the operator after 30 days, so
nothing IP-derived outlives the abuse window.

Revision ID: 0008_watch_client_hash
Revises: 0007_run_observed_at
"""

from alembic import op

revision = "0008_watch_client_hash"
down_revision = "0007_run_observed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE watch ADD COLUMN client_hash TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE watch DROP COLUMN client_hash")
