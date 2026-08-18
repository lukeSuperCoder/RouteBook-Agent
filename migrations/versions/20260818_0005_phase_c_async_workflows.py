"""Add stage C async workflow state."""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0005"
down_revision = "20260813_0004"
branch_labels = None
depends_on = None
SCHEMA = "routebook"


def upgrade() -> None:
    op.drop_constraint("run_type_valid", "workflow_runs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "run_type_valid",
        "workflow_runs",
        "run_type IN ('create','edit','refresh','finalize','recommend','plan')",
        schema=SCHEMA,
    )
    op.add_column("workflow_runs", sa.Column("phase", sa.String(48), nullable=True), schema=SCHEMA)
    op.add_column(
        "workflow_runs", sa.Column("status_message", sa.String(255), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "workflow_runs", sa.Column("latest_event_id", sa.String(64), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "latest_event_id", schema=SCHEMA)
    op.drop_column("workflow_runs", "status_message", schema=SCHEMA)
    op.drop_column("workflow_runs", "phase", schema=SCHEMA)
    op.drop_constraint("run_type_valid", "workflow_runs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "run_type_valid",
        "workflow_runs",
        "run_type IN ('create','edit','refresh','finalize')",
        schema=SCHEMA,
    )
