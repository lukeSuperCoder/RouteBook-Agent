from __future__ import annotations

from .enums import RouteBookStatus, WorkflowRunType, WorkflowStage, WorkflowStatus
from .models import (
    ConversationMessageModel,
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)
from .schemas import (
    ConversationMessageRead,
    RouteBookRead,
    RouteBookSnapshotV1,
    RouteBookVersionRead,
    WorkflowRunRead,
)


def conversation_message_read(model: ConversationMessageModel) -> ConversationMessageRead:
    return ConversationMessageRead(
        id=model.id,
        routebook_id=model.routebook_id,
        workflow_run_id=model.workflow_run_id,
        message_id=model.client_message_id,
        role=model.role,
        kind=model.kind,
        payload=model.payload_jsonb,
        created_at=model.created_at,
    )


def version_read(model: RouteBookVersionModel) -> RouteBookVersionRead:
    return RouteBookVersionRead(
        id=model.id,
        routebook_id=model.routebook_id,
        version_number=model.version_number,
        parent_version_id=model.parent_version_id,
        snapshot=RouteBookSnapshotV1.model_validate(model.snapshot_jsonb),
        change_type=model.change_type,
        change_summary=model.change_summary,
        source_user_message=model.source_user_message,
        workflow_run_id=model.workflow_run_id,
        created_at=model.created_at,
    )


def routebook_read(
    model: RouteBookModel, current_version: RouteBookVersionModel | None
) -> RouteBookRead:
    return RouteBookRead(
        id=model.id,
        title=model.title,
        status=RouteBookStatus(model.status),
        current_version_id=model.current_version_id,
        latest_final_version_id=model.latest_final_version_id,
        current_version=version_read(current_version) if current_version else None,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def workflow_run_read(model: WorkflowRunModel) -> WorkflowRunRead:
    return WorkflowRunRead(
        id=model.id,
        routebook_id=model.routebook_id,
        run_type=WorkflowRunType(model.run_type),
        base_version_id=model.base_version_id,
        result_version_id=model.result_version_id,
        status=WorkflowStatus(model.status),
        current_stage=WorkflowStage(model.current_stage),
        proposal_id=model.proposal_id,
        error_code=model.error_code,
        started_at=model.started_at,
        completed_at=model.completed_at,
        created_at=model.created_at,
    )
