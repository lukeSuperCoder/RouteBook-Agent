"""Add phase 4 recommendation and place proposal records.

Revision ID: 20260813_0003
Revises: 20260813_0002
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0003"
down_revision: str | None = "20260813_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "routebook"


def upgrade() -> None:
    op.create_table(
        "recommendation_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "base_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebook_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("strategy_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("metrics_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_recommendation_batches_routebook_created",
        "recommendation_batches",
        ["routebook_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "place_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.recommendation_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_place_id", sa.String(100), nullable=False),
        sa.Column("candidate_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("tradeoffs_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("feedback_reason", sa.String(32), nullable=True),
        sa.Column("feedback_note", sa.String(500), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("batch_id", "provider_place_id", name="proposal_batch_place"),
        sa.CheckConstraint(
            "status IN ('proposed','accepted','rejected','replaced')", name="status_valid"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_place_proposals_routebook_status",
        "place_proposals",
        ["routebook_id", "status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_place_proposals_routebook_status", table_name="place_proposals", schema=SCHEMA
    )
    op.drop_table("place_proposals", schema=SCHEMA)
    op.drop_index(
        "ix_recommendation_batches_routebook_created",
        table_name="recommendation_batches",
        schema=SCHEMA,
    )
    op.drop_table("recommendation_batches", schema=SCHEMA)
