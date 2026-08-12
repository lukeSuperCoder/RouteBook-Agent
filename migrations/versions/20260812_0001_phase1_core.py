"""Create phase 1 business core.

Revision ID: 20260812_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "routebook"


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS routebook"))
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS langgraph"))

    op.create_table(
        "routebooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_final_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('draft','planning','pending_confirmation','editable','blocked','final')",
            name="status_valid",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_type", sa.String(16), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_stage", sa.String(48), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "run_type IN ('create','edit','refresh','finalize')",
            name="run_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','interrupted','completed','failed','cancelled')",
            name="status_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workflow_runs_routebook_status",
        "workflow_runs",
        ["routebook_id", "status"],
        schema=SCHEMA,
    )
    op.create_table(
        "routebook_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "parent_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebook_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("snapshot_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_type", sa.String(24), nullable=False),
        sa.Column("change_summary", sa.String(500), nullable=False),
        sa.Column("source_user_message", sa.Text(), nullable=True),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("routebook_id", "version_number", name="routebook_version_number"),
        sa.UniqueConstraint("workflow_run_id", name="version_workflow_run"),
        sa.CheckConstraint("version_number > 0", name="version_number_positive"),
        schema=SCHEMA,
    )
    op.create_table(
        "change_proposals",
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
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("preview_snapshot_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("impact_scope_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("risk_flags_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("workflow_run_id", name="proposal_workflow_run"),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','expired')",
            name="status_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_change_proposals_routebook_status",
        "change_proposals",
        ["routebook_id", "status"],
        schema=SCHEMA,
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "routebook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("scope", "key", name="idempotency_scope_key"),
        schema=SCHEMA,
    )

    op.create_foreign_key(
        "fk_routebooks_current_version",
        "routebooks",
        "routebook_versions",
        ["current_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_routebooks_latest_final_version",
        "routebooks",
        "routebook_versions",
        ["latest_final_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_workflow_runs_base_version",
        "workflow_runs",
        "routebook_versions",
        ["base_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_workflow_runs_result_version",
        "workflow_runs",
        "routebook_versions",
        ["result_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_workflow_runs_proposal",
        "workflow_runs",
        "change_proposals",
        ["proposal_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_workflow_runs_proposal", "workflow_runs", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workflow_runs_result_version", "workflow_runs", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_workflow_runs_base_version", "workflow_runs", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_routebooks_latest_final_version", "routebooks", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_routebooks_current_version", "routebooks", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_table("idempotency_records", schema=SCHEMA)
    op.drop_index(
        "ix_change_proposals_routebook_status",
        table_name="change_proposals",
        schema=SCHEMA,
    )
    op.drop_table("change_proposals", schema=SCHEMA)
    op.drop_table("routebook_versions", schema=SCHEMA)
    op.drop_index("ix_workflow_runs_routebook_status", table_name="workflow_runs", schema=SCHEMA)
    op.drop_table("workflow_runs", schema=SCHEMA)
    op.drop_table("routebooks", schema=SCHEMA)
    op.execute(sa.text("DROP SCHEMA IF EXISTS langgraph CASCADE"))
    op.execute(sa.text("DROP SCHEMA IF EXISTS routebook CASCADE"))
