"""Allow content-enrichment workflow runs."""

from alembic import op

revision = "20260821_0007"
down_revision = "20260820_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("run_type_valid", "workflow_runs", schema="routebook", type_="check")
    op.create_check_constraint(
        "run_type_valid",
        "workflow_runs",
        "run_type IN ('create','edit','refresh','finalize','recommend','plan','enrich')",
        schema="routebook",
    )


def downgrade() -> None:
    op.drop_constraint("run_type_valid", "workflow_runs", schema="routebook", type_="check")
    op.create_check_constraint(
        "run_type_valid",
        "workflow_runs",
        "run_type IN ('create','edit','refresh','finalize','recommend','plan')",
        schema="routebook",
    )
