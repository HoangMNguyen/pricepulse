"""Product variants (colours/sizes snapshot) and labels (availability markers); UNIQLO products
keyed on (productId, priceGroup): rows already stored with a non-00 price-group URL get the
`/<pg>` suffix the adapter now produces.

Revision ID: 0006_variants_labels
Revises: 0005_summary_v3
"""

from alembic import op

revision = "0006_variants_labels"
down_revision = "0005_summary_v3"
branch_labels = None
depends_on = None

UNIQLO = "(SELECT id FROM source WHERE code = 'uniqlo')"

UPGRADE = [
    "ALTER TABLE product ADD COLUMN variants JSONB",
    "ALTER TABLE product ADD COLUMN labels JSONB NOT NULL DEFAULT '[]'",
    # The URL's last path segment is the price group; `00` keeps the bare productId.
    f"""
    UPDATE product
       SET external_id = external_id || '/' || regexp_replace(url, '^.*/', '')
     WHERE source_id = {UNIQLO}
       AND position('/' IN external_id) = 0
       AND regexp_replace(url, '^.*/', '') ~ '^[0-9]{{2}}$'
       AND regexp_replace(url, '^.*/', '') <> '00'
    """,
]

DOWNGRADE = [
    # Strip the suffix where the bare id is free; a style listed under two price groups keeps
    # the suffixed row (0005 only ever stored one of them, so nothing older depends on it).
    f"""
    UPDATE product p
       SET external_id = split_part(p.external_id, '/', 1)
     WHERE p.source_id = {UNIQLO}
       AND position('/' IN p.external_id) > 0
       AND NOT EXISTS (
             SELECT 1 FROM product o
              WHERE o.source_id = p.source_id
                AND o.external_id = split_part(p.external_id, '/', 1))
    """,
    "ALTER TABLE product DROP COLUMN labels",
    "ALTER TABLE product DROP COLUMN variants",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
