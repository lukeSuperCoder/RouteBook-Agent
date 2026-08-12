from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from .enums import (
    ChangeType,
    ProposalStatus,
    RouteBookStatus,
    WorkflowRunType,
    WorkflowStage,
    WorkflowStatus,
)
from .errors import IdempotencyConflictError, NotFoundError, VersionConflictError
from .models import (
    ChangeProposalModel,
    IdempotencyRecordModel,
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)
from .repositories import (
    IdempotencyRepository,
    ProposalRepository,
    RouteBookRepository,
    VersionRepository,
    WorkflowRunRepository,
)
from .schemas import RouteBookSnapshotV1


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_request_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CreationResult:
    routebook_id: UUID
    workflow_run_id: UUID
    reused: bool


class RouteBookService:
    IDEMPOTENCY_SCOPE = "create_routebook"

    @staticmethod
    def create(
        session: Session,
        *,
        title: str,
        idempotency_key: str,
        request_hash: str,
    ) -> CreationResult:
        records = IdempotencyRepository(session)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"{RouteBookService.IDEMPOTENCY_SCOPE}:{idempotency_key}"},
        )
        existing = records.get(RouteBookService.IDEMPOTENCY_SCOPE, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError()
            return CreationResult(
                routebook_id=existing.routebook_id,
                workflow_run_id=existing.workflow_run_id,
                reused=True,
            )

        routebook_id = uuid4()
        workflow_run_id = uuid4()
        RouteBookRepository(session).add(
            RouteBookModel(
                id=routebook_id,
                title=title,
                status=RouteBookStatus.DRAFT.value,
            )
        )
        session.flush()
        WorkflowRunRepository(session).add(
            WorkflowRunModel(
                id=workflow_run_id,
                routebook_id=routebook_id,
                run_type=WorkflowRunType.CREATE.value,
                status=WorkflowStatus.QUEUED.value,
                current_stage=WorkflowStage.QUEUED.value,
            )
        )
        session.flush()
        records.add(
            IdempotencyRecordModel(
                scope=RouteBookService.IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash,
                routebook_id=routebook_id,
                workflow_run_id=workflow_run_id,
            )
        )
        session.flush()
        return CreationResult(routebook_id, workflow_run_id, reused=False)


class VersionService:
    @staticmethod
    def commit(
        session: Session,
        *,
        routebook_id: UUID,
        workflow_run_id: UUID,
        base_version_id: UUID | None,
        snapshot: RouteBookSnapshotV1,
        change_type: ChangeType,
        change_summary: str,
        source_user_message: str | None = None,
    ) -> RouteBookVersionModel:
        workflow_run = WorkflowRunRepository(session).get(workflow_run_id, for_update=True)
        if workflow_run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        versions = VersionRepository(session)
        existing = versions.get_by_workflow_run(workflow_run_id)
        if existing is not None:
            return existing

        routebook = RouteBookRepository(session).get(routebook_id)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id != base_version_id:
            raise VersionConflictError()

        parent_version = versions.get(base_version_id) if base_version_id else None
        next_number = parent_version.version_number + 1 if parent_version else 1
        version_id = uuid4()
        version = RouteBookVersionModel(
            id=version_id,
            routebook_id=routebook_id,
            version_number=next_number,
            parent_version_id=base_version_id,
            snapshot_jsonb=snapshot.model_dump(mode="json"),
            change_type=change_type.value,
            change_summary=change_summary,
            source_user_message=source_user_message,
            workflow_run_id=workflow_run_id,
        )
        versions.add(version)
        try:
            session.flush()
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
            if constraint_name in {
                "routebook_version_number",
                "version_workflow_run",
            }:
                raise VersionConflictError() from exc
            raise

        condition: ColumnElement[bool] = RouteBookModel.current_version_id.is_(None)
        if base_version_id is not None:
            condition = RouteBookModel.current_version_id == base_version_id
        result = cast(
            CursorResult[Any],
            session.execute(
                update(RouteBookModel)
                .where(RouteBookModel.id == routebook_id, condition)
                .values(current_version_id=version_id, updated_at=utc_now())
            ),
        )
        if result.rowcount != 1:
            raise VersionConflictError()

        workflow_run.result_version_id = version_id
        workflow_run.status = WorkflowStatus.COMPLETED.value
        workflow_run.current_stage = WorkflowStage.COMPLETED.value
        workflow_run.completed_at = utc_now()
        workflow_run.error_code = None
        session.flush()
        return version

    @staticmethod
    def commit_initial(session: Session, workflow_run_id: UUID) -> RouteBookVersionModel:
        run = WorkflowRunRepository(session).get(workflow_run_id)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        return VersionService.commit(
            session,
            routebook_id=run.routebook_id,
            workflow_run_id=run.id,
            base_version_id=run.base_version_id,
            snapshot=RouteBookSnapshotV1(),
            change_type=ChangeType.CREATE,
            change_summary="创建路书",
        )


class WorkflowService:
    @staticmethod
    def mark_running(session: Session, run_id: UUID) -> WorkflowRunModel:
        run = WorkflowRunRepository(session).get(run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        if run.status == WorkflowStatus.COMPLETED.value:
            return run
        run.status = WorkflowStatus.RUNNING.value
        run.current_stage = WorkflowStage.SAVING_VERSION.value
        run.started_at = run.started_at or utc_now()
        run.error_code = None
        session.flush()
        return run

    @staticmethod
    def mark_failed(session: Session, run_id: UUID, error_code: str) -> WorkflowRunModel:
        run = WorkflowRunRepository(session).get(run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        if run.status != WorkflowStatus.COMPLETED.value:
            run.status = WorkflowStatus.FAILED.value
            run.current_stage = WorkflowStage.FAILED.value
            run.error_code = error_code
            run.completed_at = utc_now()
            session.flush()
        return run


class ProposalService:
    @staticmethod
    def create_pending(
        session: Session,
        *,
        routebook_id: UUID,
        base_version_id: UUID,
        workflow_run_id: UUID,
        preview: RouteBookSnapshotV1,
        impact_scope: dict[str, object],
        risk_flags: list[dict[str, object]],
    ) -> ChangeProposalModel:
        proposal = ChangeProposalModel(
            routebook_id=routebook_id,
            base_version_id=base_version_id,
            workflow_run_id=workflow_run_id,
            preview_snapshot_jsonb=preview.model_dump(mode="json"),
            impact_scope_jsonb=impact_scope,
            risk_flags_jsonb=risk_flags,
            status=ProposalStatus.PENDING.value,
        )
        ProposalRepository(session).add(proposal)
        session.flush()
        return proposal

    @staticmethod
    def resolve(session: Session, proposal_id: UUID, status: ProposalStatus) -> ChangeProposalModel:
        if status not in {ProposalStatus.ACCEPTED, ProposalStatus.REJECTED, ProposalStatus.EXPIRED}:
            raise ValueError("proposal can only transition out of pending")
        proposal = ProposalRepository(session).get(proposal_id, for_update=True)
        if proposal is None:
            raise NotFoundError(details={"resource": "proposal"})
        if proposal.status != ProposalStatus.PENDING.value:
            return proposal
        proposal.status = status.value
        proposal.resolved_at = utc_now()
        session.flush()
        return proposal
