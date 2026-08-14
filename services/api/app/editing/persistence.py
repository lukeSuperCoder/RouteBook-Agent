from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from ..enums import (
    ChangeType,
    ProposalStatus,
    RouteBookStatus,
    WorkflowRunType,
    WorkflowStage,
    WorkflowStatus,
)
from ..errors import (
    IdempotencyConflictError,
    NotFoundError,
    VersionConflictError,
    WorkflowStateConflictError,
)
from ..models import ChangeProposalModel, WorkflowRunModel, utc_now
from ..repositories import (
    ProposalRepository,
    RouteBookRepository,
    VersionRepository,
    WorkflowRunRepository,
)
from ..schemas import RouteBookSnapshotV1
from ..services import VersionService
from .models import EditPlan
from .service import EditingService


@dataclass(frozen=True)
class EditExecutionResult:
    proposal: ChangeProposalModel | None = None
    version_id: UUID | None = None
    reused: bool = False


class EditingPersistenceService:
    @staticmethod
    def load_current(session: Session, routebook_id: UUID) -> tuple[UUID, RouteBookSnapshotV1]:
        routebook = RouteBookRepository(session).get(routebook_id)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id is None:
            raise WorkflowStateConflictError(details={"reason": "routebook_has_no_version"})
        version = VersionRepository(session).get(routebook.current_version_id)
        if version is None:
            raise NotFoundError(details={"resource": "routebook_version"})
        return version.id, RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)

    @staticmethod
    def execute(
        session: Session,
        *,
        routebook_id: UUID,
        base_version_id: UUID,
        base_snapshot: RouteBookSnapshotV1,
        plan: EditPlan,
        operation_id: UUID,
    ) -> EditExecutionResult:
        if not plan.resolution.resolved or plan.preview is None:
            raise WorkflowStateConflictError(
                details={
                    "reason": "reference_ambiguous",
                    "clarification": plan.resolution.clarification,
                    "candidates": plan.resolution.candidates,
                }
            )
        fingerprint = _plan_fingerprint(plan)
        existing_run = WorkflowRunRepository(session).get(operation_id)
        if existing_run is not None:
            if existing_run.routebook_id != routebook_id:
                raise IdempotencyConflictError()
            proposal = (
                ProposalRepository(session).get(existing_run.proposal_id)
                if existing_run.proposal_id
                else None
            )
            stored_fingerprint = (
                str(proposal.impact_scope_jsonb.get("operation_fingerprint", ""))
                if proposal
                else ""
            )
            if proposal is None and existing_run.result_version_id:
                existing_version = VersionRepository(session).get(
                    existing_run.result_version_id
                )
                stored_fingerprint = (
                    existing_version.source_user_message if existing_version else ""
                ) or ""
            if stored_fingerprint != fingerprint:
                raise IdempotencyConflictError()
            return EditExecutionResult(
                proposal=proposal,
                version_id=existing_run.result_version_id,
                reused=True,
            )
        if not EditingService.validate_unchanged_days(
            base_snapshot, plan.preview, plan.impact.affected_days
        ):
            raise WorkflowStateConflictError(details={"reason": "UNEXPECTED_SCOPE_CHANGE"})
        run = WorkflowRunModel(
            id=operation_id,
            routebook_id=routebook_id,
            run_type=WorkflowRunType.EDIT.value,
            base_version_id=base_version_id,
            status=WorkflowStatus.RUNNING.value,
            current_stage=WorkflowStage.VALIDATING.value,
            started_at=utc_now(),
        )
        WorkflowRunRepository(session).add(run)
        session.flush()
        if plan.impact.requires_confirmation:
            proposal = ChangeProposalModel(
                routebook_id=routebook_id,
                base_version_id=base_version_id,
                workflow_run_id=run.id,
                preview_snapshot_jsonb=plan.preview.model_dump(mode="json"),
                impact_scope_jsonb={
                    **plan.impact.model_dump(mode="json"),
                    "operation_fingerprint": fingerprint,
                },
                risk_flags_jsonb=[item.model_dump(mode="json") for item in plan.risks],
                status=ProposalStatus.PENDING.value,
            )
            ProposalRepository(session).add(proposal)
            session.flush()
            run.proposal_id = proposal.id
            run.status = WorkflowStatus.INTERRUPTED.value
            run.current_stage = WorkflowStage.WAITING_FOR_CHANGE_CONFIRMATION.value
            routebook = RouteBookRepository(session).get(routebook_id)
            if routebook is not None:
                routebook.status = RouteBookStatus.PENDING_CONFIRMATION.value
            session.flush()
            return EditExecutionResult(proposal=proposal)
        version = VersionService.commit(
            session,
            routebook_id=routebook_id,
            workflow_run_id=run.id,
            base_version_id=base_version_id,
            snapshot=plan.preview,
            change_type=ChangeType.EDIT,
            change_summary=plan.change_summary,
            source_user_message=fingerprint,
        )
        return EditExecutionResult(version_id=version.id)

    @staticmethod
    def resolve_proposal(
        session: Session, *, proposal_id: UUID, accept: bool
    ) -> EditExecutionResult:
        proposal = ProposalRepository(session).get(proposal_id, for_update=True)
        if proposal is None:
            raise NotFoundError(details={"resource": "change_proposal"})
        run = WorkflowRunRepository(session).get(proposal.workflow_run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        if proposal.status != ProposalStatus.PENDING.value:
            expected = ProposalStatus.ACCEPTED.value if accept else ProposalStatus.REJECTED.value
            if proposal.status == expected:
                return EditExecutionResult(
                    proposal=proposal, version_id=run.result_version_id, reused=True
                )
            raise WorkflowStateConflictError(details={"status": proposal.status})
        routebook = RouteBookRepository(session).get(proposal.routebook_id, for_update=True)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id != proposal.base_version_id:
            proposal.status = ProposalStatus.EXPIRED.value
            proposal.resolved_at = utc_now()
            run.status = WorkflowStatus.FAILED.value
            run.current_stage = WorkflowStage.FAILED.value
            run.error_code = "VERSION_CONFLICT"
            session.flush()
            raise VersionConflictError()
        if not accept:
            proposal.status = ProposalStatus.REJECTED.value
            proposal.resolved_at = utc_now()
            run.status = WorkflowStatus.CANCELLED.value
            run.completed_at = utc_now()
            routebook.status = RouteBookStatus.EDITABLE.value
            session.flush()
            return EditExecutionResult(proposal=proposal)
        version = VersionService.commit(
            session,
            routebook_id=proposal.routebook_id,
            workflow_run_id=proposal.workflow_run_id,
            base_version_id=proposal.base_version_id,
            snapshot=RouteBookSnapshotV1.model_validate(proposal.preview_snapshot_jsonb),
            change_type=ChangeType.EDIT,
            change_summary="确认重要修改提案",
        )
        proposal.status = ProposalStatus.ACCEPTED.value
        proposal.resolved_at = utc_now()
        routebook.status = RouteBookStatus.EDITABLE.value
        session.flush()
        return EditExecutionResult(proposal=proposal, version_id=version.id)

    @staticmethod
    def undo(session: Session, *, routebook_id: UUID, operation_id: UUID) -> EditExecutionResult:
        existing_run = WorkflowRunRepository(session).get(operation_id)
        if existing_run is not None:
            if existing_run.routebook_id != routebook_id:
                raise IdempotencyConflictError()
            return EditExecutionResult(version_id=existing_run.result_version_id, reused=True)
        routebook = RouteBookRepository(session).get(routebook_id, for_update=True)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id is None:
            raise WorkflowStateConflictError(details={"reason": "nothing_to_undo"})
        current = VersionRepository(session).get(routebook.current_version_id)
        if current is None or current.parent_version_id is None:
            raise WorkflowStateConflictError(details={"reason": "nothing_to_undo"})
        parent = VersionRepository(session).get(current.parent_version_id)
        if parent is None:
            raise NotFoundError(details={"resource": "parent_version"})
        run = WorkflowRunModel(
            id=operation_id,
            routebook_id=routebook_id,
            run_type=WorkflowRunType.EDIT.value,
            base_version_id=current.id,
            status=WorkflowStatus.RUNNING.value,
            current_stage=WorkflowStage.SAVING_VERSION.value,
            started_at=utc_now(),
        )
        WorkflowRunRepository(session).add(run)
        session.flush()
        version = VersionService.commit(
            session,
            routebook_id=routebook_id,
            workflow_run_id=run.id,
            base_version_id=current.id,
            snapshot=RouteBookSnapshotV1.model_validate(parent.snapshot_jsonb),
            change_type=ChangeType.UNDO,
            change_summary=f"撤销版本 {current.version_number}",
        )
        routebook.status = RouteBookStatus.EDITABLE.value
        session.flush()
        return EditExecutionResult(version_id=version.id)


def _plan_fingerprint(plan: EditPlan) -> str:
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
