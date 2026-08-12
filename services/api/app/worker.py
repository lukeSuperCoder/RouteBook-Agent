from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import Celery
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .db import SessionFactory
from .enums import WorkflowStage, WorkflowStatus
from .errors import AppError
from .observability import (
    configure_logging,
    request_id_context,
    routebook_id_context,
    workflow_run_id_context,
)
from .progress import ProgressPublisher, build_progress_event
from .repositories import VersionRepository, WorkflowRunRepository
from .services import WorkflowService
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
