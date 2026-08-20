"""Add soft deletion for routebook history management."""

import sqlalchemy as sa
from alembic import op

revision = "20260820_0006"
down_revision = "20260818_0005"
branch_labels = None
depends_on = None
SCHEMA = "routebook"


def upgrade() -> None:
    op.add_column(
        "routebooks",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_routebooks_active_updated",
        "routebooks",
        ["deleted_at", "updated_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_routebooks_active_updated", table_name="routebooks", schema=SCHEMA)
    op.drop_column("routebooks", "deleted_at", schema=SCHEMA)
