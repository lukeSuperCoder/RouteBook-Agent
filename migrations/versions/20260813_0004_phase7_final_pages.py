"""Add immutable final page records.

Revision ID: 20260813_0004
Revises: 20260813_0003
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0004"
down_revision: str | None = "20260813_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
SCHEMA = "routebook"


def upgrade() -> None:
    op.create_table(
        "final_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "routebook_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebook_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("public_token_hash", sa.String(64), nullable=False),
        sa.Column("privacy_policy", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("public_token_hash", name="final_page_public_token_hash"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_final_pages_routebook_version",
        "final_pages",
        ["routebook_id", "routebook_version_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_final_pages_routebook_version", table_name="final_pages", schema=SCHEMA)
    op.drop_table("final_pages", schema=SCHEMA)
