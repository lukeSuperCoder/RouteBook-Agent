"""Add phase 3 requirement conversation records.

Revision ID: 20260813_0002
Revises: 20260812_0001
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_0002"
down_revision: str | None = "20260812_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "routebook"


def upgrade() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("client_message_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("payload_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("routebook_id", "client_message_id", name="message_client_id"),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="role_valid"),
        sa.CheckConstraint(
            "kind IN ('requirement_input','requirement_clarification','status')",
            name="kind_valid",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_conversation_messages_routebook_created",
        "conversation_messages",
        ["routebook_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "llm_call_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.conversation_messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("response_id", sa.String(160), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output_jsonb", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "message_id", "attempt_count", name="llm_call_attempt"
        ),
        sa.CheckConstraint("status IN ('succeeded','failed')", name="status_valid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_call_records_workflow",
        "llm_call_records",
        ["workflow_run_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_llm_call_records_workflow", table_name="llm_call_records", schema=SCHEMA)
    op.drop_table("llm_call_records", schema=SCHEMA)
    op.drop_index(
        "ix_conversation_messages_routebook_created",
        table_name="conversation_messages",
        schema=SCHEMA,
    )
    op.drop_table("conversation_messages", schema=SCHEMA)
