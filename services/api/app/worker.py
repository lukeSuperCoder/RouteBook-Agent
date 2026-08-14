from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import Celery
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .db import SessionFactory
from .enums import ChangeType, WorkflowStage, WorkflowStatus
from .errors import AppError
from .observability import (
    configure_logging,
    request_id_context,
    routebook_id_context,
    workflow_run_id_context,
)
from .progress import ProgressPublisher, build_progress_event
from .repositories import ConversationMessageRepository, VersionRepository, WorkflowRunRepository
from .requirements import (
    AnthropicRequirementExtractor,
    ClarificationAnswer,
    RequirementDecision,
    RequirementPatch,
    RequirementService,
    build_requirement_graph,
    initial_requirement_state,
)
from .schemas import RouteBookSnapshotV1
from .services import RequirementMessageService, VersionService, WorkflowService
from .workflows import FoundationWorkflowState, build_foundation_graph

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("routebook.worker")

celery_app = Celery("routebook", broker=settings.celery_broker_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
)


def dispatch_workflow(run_id: UUID, request_id: str) -> None:
    celery_app.send_task(
        "routebook.execute_foundation_workflow",
        args=[str(run_id), request_id],
        task_id=str(run_id),
        headers={"request_id": request_id, "workflow_run_id": str(run_id)},
    )


def dispatch_requirement_workflow(
    run_id: UUID, message_id: UUID, request_id: str, *, resume: bool = False
) -> None:
    celery_app.send_task(
        "routebook.execute_requirement_workflow",
        args=[str(run_id), str(message_id), request_id, resume],
        task_id=f"{run_id}:{message_id}",
        headers={"request_id": request_id, "workflow_run_id": str(run_id)},
    )


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="routebook.execute_foundation_workflow",
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_foundation_workflow(self: Any, run_id_text: str, request_id: str) -> None:
    run_id = UUID(run_id_text)
    request_id_context.set(request_id)
    workflow_run_id_context.set(run_id_text)
    publisher = ProgressPublisher(settings.redis_url)

    try:
        with SessionFactory() as session:
            run = WorkflowRunRepository(session).get(run_id)
            if run is None:
                raise RuntimeError(f"workflow run not found: {run_id}")
            routebook_id_context.set(str(run.routebook_id))
            existing = VersionRepository(session).get_by_workflow_run(run_id)
            if existing is not None and run.status == WorkflowStatus.COMPLETED.value:
                publisher.publish(
                    build_progress_event(
                        run_id=run_id,
                        routebook_id=run.routebook_id,
                        stage=WorkflowStage.COMPLETED,
                        status=WorkflowStatus.COMPLETED,
                        message="重复任务已复用现有版本",
                        completed=2,
                        total=2,
                    )
                )
                return
            initial_state: FoundationWorkflowState = {
                "workflow_run_id": run_id_text,
                "routebook_id": str(run.routebook_id),
                "base_version_id": str(run.base_version_id) if run.base_version_id else None,
                "job_stage": run.current_stage,
                "result_version_id": None,
                "warnings": [],
                "errors": [],
            }

        with PostgresSaver.from_conn_string(settings.langgraph_database_url) as checkpointer:
            graph = build_foundation_graph(checkpointer, publisher)
            graph.invoke(initial_state, config={"configurable": {"thread_id": run_id_text}})
    except OperationalError as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(2**self.request.retries, 30)) from exc
        _mark_failed(run_id, "DEPENDENCY_UNAVAILABLE", publisher)
        raise
    except AppError as exc:
        _mark_failed(run_id, exc.code, publisher)
        raise
    except Exception:
        log.exception("foundation workflow failed")
        _mark_failed(run_id, "INTERNAL_ERROR", publisher)
        raise


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="routebook.execute_requirement_workflow",
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def execute_requirement_workflow(
    self: Any,
    run_id_text: str,
    message_id_text: str,
    request_id: str,
    resume: bool = False,
) -> None:
    run_id = UUID(run_id_text)
    message_id = UUID(message_id_text)
    request_id_context.set(request_id)
    workflow_run_id_context.set(run_id_text)
    publisher = ProgressPublisher(settings.redis_url)

    try:
        with SessionFactory.begin() as session:
            run = WorkflowService.mark_requirement_running(session, run_id)
            message = ConversationMessageRepository(session).get(message_id)
            if message is None or message.workflow_run_id != run_id:
                raise RuntimeError("requirement message not found for workflow")
            routebook_id = run.routebook_id
            base_version_id = run.base_version_id
            client_message_id = message.client_message_id
            routebook_id_context.set(str(routebook_id))
            if run.status == WorkflowStatus.COMPLETED.value:
                return
            base = (
                VersionRepository(session).get(base_version_id)
                if base_version_id
                else None
            )
            snapshot = (
                RouteBookSnapshotV1.model_validate(base.snapshot_jsonb)
                if base is not None
                else RouteBookSnapshotV1()
            )
            message_text = str(message.payload_jsonb["text"])

        publisher.publish(
            build_progress_event(
                run_id=run_id,
                routebook_id=routebook_id,
                stage=WorkflowStage.EXTRACTING_REQUIREMENTS,
                status=WorkflowStatus.RUNNING,
                message="正在理解旅行需求",
                completed=0,
                total=2,
            )
        )
        extractor = AnthropicRequirementExtractor(settings)
        service = RequirementService()
        with PostgresSaver.from_conn_string(settings.langgraph_database_url) as checkpointer:
            graph = build_requirement_graph(
                extractor=extractor,
                service=service,
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": run_id_text}}
            if resume:
                result = graph.invoke(
                    Command(
                        resume=ClarificationAnswer(
                            message_id=client_message_id,
                            text=message_text,
                        ).model_dump(mode="json")
                    ),
                    config=config,
                )
            else:
                result = graph.invoke(
                    initial_requirement_state(
                        workflow_run_id=run_id_text,
                        routebook_id=str(routebook_id),
                        message_id=client_message_id,
                        user_message=message_text,
                        requirements=snapshot.requirements,
                    ),
                    config=config,
                )

        decision = RequirementDecision.model_validate(result["requirement_decision"])
        patch = RequirementPatch.model_validate(result["requirement_patch"] or {})
        traces = result.get("extraction_traces", [])
        with SessionFactory.begin() as session:
            if traces:
                from .requirements import ExtractionTrace

                RequirementMessageService.record_trace(
                    session,
                    run_id=run_id,
                    message_id=message_id,
                    trace=ExtractionTrace.model_validate(traces[-1]),
                    patch=patch,
                )
            elif result.get("extraction_failed"):
                RequirementMessageService.record_failed_trace(
                    session,
                    run_id=run_id,
                    message_id=message_id,
                    prompt_version=settings.requirement_prompt_version,
                    model=settings.model_id,
                    attempt_count=settings.requirement_max_attempts,
                )
            if not decision.ready:
                interrupt_payload = result["__interrupt__"][0].value
                RequirementMessageService.record_clarification(
                    session,
                    run_id=run_id,
                    trigger_message_id=client_message_id,
                    payload=interrupt_payload,
                )
                WorkflowService.mark_interrupted(session, run_id)
            else:
                updated = snapshot.model_copy(update={"requirements": decision.snapshot})
                resolved_base_version_id = VersionService.resolve_requirement_base(
                    session,
                    routebook_id=routebook_id,
                    base_version_id=base_version_id,
                )
                VersionService.commit(
                    session,
                    routebook_id=routebook_id,
                    workflow_run_id=run_id,
                    base_version_id=resolved_base_version_id,
                    snapshot=updated,
                    change_type=(
                        ChangeType.EDIT if resolved_base_version_id else ChangeType.CREATE
                    ),
                    change_summary="更新旅行需求",
                    source_user_message=message_text,
                )

        if decision.ready:
            publisher.publish(
                build_progress_event(
                    run_id=run_id,
                    routebook_id=routebook_id,
                    stage=WorkflowStage.COMPLETED,
                    status=WorkflowStatus.COMPLETED,
                    message="旅行需求已确认",
                    completed=2,
                    total=2,
                )
            )
        else:
            publisher.publish(
                build_progress_event(
                    run_id=run_id,
                    routebook_id=routebook_id,
                    stage=WorkflowStage.WAITING_FOR_CLARIFICATION,
                    status=WorkflowStatus.INTERRUPTED,
                    message="还需要补充少量关键信息",
                    completed=1,
                    total=2,
                )
            )
    except OperationalError as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(2**self.request.retries, 30)) from exc
        _mark_failed(run_id, "DEPENDENCY_UNAVAILABLE", publisher)
        raise
    except AppError as exc:
        _mark_failed(run_id, exc.code, publisher)
        raise
    except Exception:
        log.exception("requirement workflow failed")
        _mark_failed(run_id, "INTERNAL_ERROR", publisher)
        raise


def _mark_failed(run_id: UUID, error_code: str, publisher: ProgressPublisher) -> None:
    try:
        with SessionFactory.begin() as session:
            run = WorkflowService.mark_failed(session, run_id, error_code)
            routebook_id = run.routebook_id
        publisher.publish(
            build_progress_event(
                run_id=run_id,
                routebook_id=routebook_id,
                stage=WorkflowStage.FAILED,
                status=WorkflowStatus.FAILED,
                message="工作流执行失败，请稍后重试",
                completed=0,
                total=2,
            )
        )
    except Exception:
        log.exception("failed to persist workflow failure run_id=%s", run_id)
