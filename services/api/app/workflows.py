from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from .db import SessionFactory
from .enums import WorkflowStage, WorkflowStatus
from .progress import ProgressPublisher, build_progress_event
from .services import VersionService, WorkflowService


class FoundationWorkflowState(TypedDict):
    workflow_run_id: str
    routebook_id: str
    base_version_id: str | None
    job_stage: str
    result_version_id: str | None
    warnings: list[dict[str, object]]
    errors: list[dict[str, object]]


def build_foundation_graph(checkpointer: Any, publisher: ProgressPublisher) -> Any:
    def save_initial_version(state: FoundationWorkflowState) -> dict[str, str]:
        run_id = UUID(state["workflow_run_id"])
        with SessionFactory.begin() as session:
            run = WorkflowService.mark_running(session, run_id)
            routebook_id = run.routebook_id

        publisher.publish(
            build_progress_event(
                run_id=run_id,
                routebook_id=routebook_id,
                stage=WorkflowStage.SAVING_VERSION,
                status=WorkflowStatus.RUNNING,
                message="正在保存路书初始版本",
                completed=1,
                total=2,
            )
        )

        with SessionFactory.begin() as session:
            version = VersionService.commit_initial(session, run_id)

        publisher.publish(
            build_progress_event(
                run_id=run_id,
                routebook_id=routebook_id,
                stage=WorkflowStage.COMPLETED,
                status=WorkflowStatus.COMPLETED,
                message="路书工程基线已就绪",
                completed=2,
                total=2,
            )
        )
        return {
            "job_stage": WorkflowStage.COMPLETED.value,
            "result_version_id": str(version.id),
        }

    builder = StateGraph(FoundationWorkflowState)
    builder.add_node("save_initial_version", save_initial_version)
    builder.add_edge(START, "save_initial_version")
    builder.add_edge("save_initial_version", END)
    return builder.compile(checkpointer=checkpointer)
