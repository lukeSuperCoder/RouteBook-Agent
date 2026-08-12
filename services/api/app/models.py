from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

SCHEMA = "routebook"


def utc_now() -> datetime:
    return datetime.now(UTC)


ROUTEBOOK_STATUSES = "'draft','planning','pending_confirmation','editable','blocked','final'"
WORKFLOW_STATUSES = "'queued','running','interrupted','completed','failed','cancelled'"
WORKFLOW_TYPES = "'create','edit','refresh','finalize'"
PROPOSAL_STATUSES = "'pending','accepted','rejected','expired'"


class RouteBookModel(Base):
    __tablename__ = "routebooks"
    __table_args__ = (
        CheckConstraint(f"status IN ({ROUTEBOOK_STATUSES})", name="status_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.routebook_versions.id",
            name="fk_routebooks_current_version",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    latest_final_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.routebook_versions.id",
            name="fk_routebooks_latest_final_version",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class WorkflowRunModel(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(f"run_type IN ({WORKFLOW_TYPES})", name="run_type_valid"),
        CheckConstraint(f"status IN ({WORKFLOW_STATUSES})", name="status_valid"),
        Index("ix_workflow_runs_routebook_status", "routebook_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    routebook_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"), nullable=False
    )
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    base_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.routebook_versions.id",
            name="fk_workflow_runs_base_version",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    result_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.routebook_versions.id",
            name="fk_workflow_runs_result_version",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    current_stage: Mapped[str] = mapped_column(String(48), nullable=False, default="queued")
    proposal_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.change_proposals.id",
            name="fk_workflow_runs_proposal",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RouteBookVersionModel(Base):
    __tablename__ = "routebook_versions"
    __table_args__ = (
        UniqueConstraint("routebook_id", "version_number", name="routebook_version_number"),
        UniqueConstraint("workflow_run_id", name="version_workflow_run"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    routebook_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebook_versions.id", ondelete="RESTRICT"), nullable=True
    )
    snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_type: Mapped[str] = mapped_column(String(24), nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    source_user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ChangeProposalModel(Base):
    __tablename__ = "change_proposals"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="proposal_workflow_run"),
        CheckConstraint(f"status IN ({PROPOSAL_STATUSES})", name="status_valid"),
        Index("ix_change_proposals_routebook_status", "routebook_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    routebook_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"), nullable=False
    )
    base_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebook_versions.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    preview_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    impact_scope_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_flags_jsonb: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecordModel(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "key", name="idempotency_scope_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    routebook_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.routebooks.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workflow_runs.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
