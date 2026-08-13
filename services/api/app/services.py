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
from .errors import (
    IdempotencyConflictError,
    NotFoundError,
    VersionConflictError,
    WorkflowStateConflictError,
)
from .models import (
    ChangeProposalModel,
    ConversationMessageModel,
    IdempotencyRecordModel,
    LlmCallRecordModel,
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)
from .repositories import (
    ConversationMessageRepository,
    IdempotencyRepository,
    LlmCallRecordRepository,
    ProposalRepository,
    RouteBookRepository,
    VersionRepository,
    WorkflowRunRepository,
)
from .requirements.models import ExtractionTrace, RequirementPatch
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

    @staticmethod
    def mark_requirement_running(session: Session, run_id: UUID) -> WorkflowRunModel:
        run = WorkflowRunRepository(session).get(run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        if run.status == WorkflowStatus.COMPLETED.value:
            return run
        run.status = WorkflowStatus.RUNNING.value
        run.current_stage = WorkflowStage.EXTRACTING_REQUIREMENTS.value
        run.started_at = run.started_at or utc_now()
        run.error_code = None
        routebook = RouteBookRepository(session).get(run.routebook_id)
        if routebook is not None:
            routebook.status = RouteBookStatus.PLANNING.value
        session.flush()
        return run

    @staticmethod
    def mark_interrupted(session: Session, run_id: UUID) -> WorkflowRunModel:
        run = WorkflowRunRepository(session).get(run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        if run.status != WorkflowStatus.COMPLETED.value:
            run.status = WorkflowStatus.INTERRUPTED.value
            run.current_stage = WorkflowStage.WAITING_FOR_CLARIFICATION.value
            routebook = RouteBookRepository(session).get(run.routebook_id)
            if routebook is not None:
                routebook.status = RouteBookStatus.PENDING_CONFIRMATION.value
            session.flush()
        return run


@dataclass(frozen=True)
class MessageWorkflowResult:
    message: ConversationMessageModel
    workflow_run_id: UUID
    workflow_status: WorkflowStatus
    reused: bool
    should_dispatch: bool


class RequirementMessageService:
    @staticmethod
    def start(
        session: Session,
        *,
        routebook_id: UUID,
        client_message_id: str,
        text: str,
    ) -> MessageWorkflowResult:
        routebook = RouteBookRepository(session).get(routebook_id, for_update=True)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        messages = ConversationMessageRepository(session)
        existing = messages.get_by_client_id(routebook_id, client_message_id)
        if existing is not None:
            if existing.payload_jsonb.get("text") != text:
                raise IdempotencyConflictError()
            existing_run = WorkflowRunRepository(session).get(existing.workflow_run_id)
            return MessageWorkflowResult(
                existing,
                existing.workflow_run_id,
                WorkflowStatus(existing_run.status) if existing_run else WorkflowStatus.FAILED,
                True,
                existing_run is not None
                and existing_run.status == WorkflowStatus.QUEUED.value,
            )

        run = WorkflowRunModel(
            routebook_id=routebook_id,
            run_type=(
                WorkflowRunType.EDIT.value
                if routebook.current_version_id is not None
                else WorkflowRunType.CREATE.value
            ),
            base_version_id=routebook.current_version_id,
            status=WorkflowStatus.QUEUED.value,
            current_stage=WorkflowStage.QUEUED.value,
        )
        WorkflowRunRepository(session).add(run)
        session.flush()
        message = ConversationMessageModel(
            routebook_id=routebook_id,
            workflow_run_id=run.id,
            client_message_id=client_message_id,
            role="user",
            kind="requirement_input",
            payload_jsonb={"text": text},
        )
        messages.add(message)
        session.flush()
        return MessageWorkflowResult(message, run.id, WorkflowStatus.QUEUED, False, True)

    @staticmethod
    def resume(
        session: Session,
        *,
        run_id: UUID,
        client_message_id: str,
        text: str,
    ) -> MessageWorkflowResult:
        run = WorkflowRunRepository(session).get(run_id, for_update=True)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        messages = ConversationMessageRepository(session)
        existing = messages.get_by_client_id(run.routebook_id, client_message_id)
        if existing is not None:
            if existing.workflow_run_id != run_id or existing.payload_jsonb.get("text") != text:
                raise IdempotencyConflictError()
            return MessageWorkflowResult(
                existing,
                run_id,
                WorkflowStatus(run.status),
                True,
                run.status == WorkflowStatus.QUEUED.value,
            )
        if run.status != WorkflowStatus.INTERRUPTED.value:
            raise WorkflowStateConflictError(
                details={"status": run.status, "expected": WorkflowStatus.INTERRUPTED.value}
            )
        message = ConversationMessageModel(
            routebook_id=run.routebook_id,
            workflow_run_id=run_id,
            client_message_id=client_message_id,
            role="user",
            kind="requirement_clarification",
            payload_jsonb={"text": text},
        )
        messages.add(message)
        run.status = WorkflowStatus.QUEUED.value
        run.current_stage = WorkflowStage.QUEUED.value
        routebook = RouteBookRepository(session).get(run.routebook_id)
        if routebook is not None:
            routebook.status = RouteBookStatus.PLANNING.value
        session.flush()
        return MessageWorkflowResult(message, run_id, WorkflowStatus.QUEUED, False, True)

    @staticmethod
    def record_clarification(
        session: Session,
        *,
        run_id: UUID,
        trigger_message_id: str,
        payload: dict[str, Any],
    ) -> ConversationMessageModel:
        run = WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        client_message_id = f"system-clarification-{trigger_message_id}"
        messages = ConversationMessageRepository(session)
        existing = messages.get_by_client_id(run.routebook_id, client_message_id)
        if existing is not None:
            existing.payload_jsonb = payload
            session.flush()
            return existing
        message = ConversationMessageModel(
            routebook_id=run.routebook_id,
            workflow_run_id=run_id,
            client_message_id=client_message_id,
            role="assistant",
            kind="requirement_clarification",
            payload_jsonb=payload,
        )
        messages.add(message)
        session.flush()
        return message

    @staticmethod
    def record_failed_trace(
        session: Session,
        *,
        run_id: UUID,
        message_id: UUID,
        prompt_version: str,
        model: str,
        attempt_count: int,
    ) -> None:
        records = LlmCallRecordRepository(session)
        if records.exists(run_id, message_id, attempt_count):
            return
        records.add(
            LlmCallRecordModel(
                workflow_run_id=run_id,
                message_id=message_id,
                prompt_version=prompt_version,
                model=model,
                attempt_count=attempt_count,
                latency_ms=0,
                status="failed",
                output_jsonb=None,
                error_code="REQUIREMENT_EXTRACTION_FAILED",
            )
        )
        session.flush()

    @staticmethod
    def record_trace(
        session: Session,
        *,
        run_id: UUID,
        message_id: UUID,
        trace: ExtractionTrace,
        patch: RequirementPatch,
    ) -> None:
        records = LlmCallRecordRepository(session)
        if records.exists(run_id, message_id, trace.attempt_count):
            return
        records.add(
            LlmCallRecordModel(
                workflow_run_id=run_id,
                message_id=message_id,
                prompt_version=trace.prompt_version,
                model=trace.model,
                response_id=trace.response_id,
                attempt_count=trace.attempt_count,
                latency_ms=trace.latency_ms,
                input_tokens=trace.input_tokens,
                output_tokens=trace.output_tokens,
                status="succeeded",
                output_jsonb=patch.model_dump(mode="json"),
            )
        )
        session.flush()


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
