from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

if os.getenv("RUN_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "set RUN_INTEGRATION_TESTS=1 to run service integration tests",
        allow_module_level=True,
    )

from services.api.app.checkpoint_setup import main as setup_checkpointer
from services.api.app.db import SessionFactory
from services.api.app.enums import ChangeType, WorkflowRunType, WorkflowStage, WorkflowStatus
from services.api.app.errors import VersionConflictError
from services.api.app.main import create_app
from services.api.app.models import (
    ConversationMessageModel,
    LlmCallRecordModel,
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)
from services.api.app.requirements import (
    ExtractionResult,
    ExtractionTrace,
    RequirementPatch,
    RequirementPatchValue,
)
from services.api.app.schemas import RouteBookSnapshotV1
from services.api.app.services import RequirementMessageService, VersionService, WorkflowService
from services.api.app.worker import execute_foundation_workflow, execute_requirement_workflow

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_database():
    with SessionFactory.begin() as session:
        session.execute(
            text(
                "TRUNCATE routebook.idempotency_records, "
                "routebook.llm_call_records, "
                "routebook.conversation_messages, "
                "routebook.change_proposals, "
                "routebook.routebook_versions, "
                "routebook.workflow_runs, "
                "routebook.routebooks CASCADE"
            )
        )
    yield


def test_create_api_is_idempotent_and_rejects_key_reuse() -> None:
    dispatched: list[UUID] = []
    app = create_app(lambda run_id, _request_id: dispatched.append(run_id))

    with TestClient(app) as client:
        headers = {"Idempotency-Key": "create-routebook-001"}
        first = client.post("/api/routebooks", json={"title": "武汉三日路书"}, headers=headers)
        repeated = client.post("/api/routebooks", json={"title": "武汉三日路书"}, headers=headers)
        conflict = client.post("/api/routebooks", json={"title": "南京路书"}, headers=headers)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json()["routebook_id"] == first.json()["routebook_id"]
    assert repeated.json()["workflow_run_id"] == first.json()["workflow_run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert dispatched == [UUID(first.json()["workflow_run_id"])] * 2


def test_foundation_worker_commits_one_version_when_redelivered() -> None:
    run_id = _create_routebook_and_run()
    setup_checkpointer()

    execute_foundation_workflow.run(str(run_id), "integration-request")
    execute_foundation_workflow.run(str(run_id), "integration-request")

    with SessionFactory() as session:
        run = session.get(WorkflowRunModel, run_id)
        assert run is not None
        count = session.scalar(
            select(func.count())
            .select_from(RouteBookVersionModel)
            .where(RouteBookVersionModel.workflow_run_id == run_id)
        )
        assert count == 1
        assert run.status == WorkflowStatus.COMPLETED.value
        assert run.current_stage == WorkflowStage.COMPLETED.value
        assert run.result_version_id is not None


def test_stale_base_version_returns_version_conflict() -> None:
    initial_run_id = _create_routebook_and_run()
    setup_checkpointer()
    execute_foundation_workflow.run(str(initial_run_id), "integration-request")

    with SessionFactory.begin() as session:
        initial_run = session.get(WorkflowRunModel, initial_run_id)
        assert initial_run is not None and initial_run.result_version_id is not None
        base_version_id = initial_run.result_version_id
        routebook_id = initial_run.routebook_id
        first_edit = WorkflowRunModel(
            routebook_id=routebook_id,
            run_type=WorkflowRunType.EDIT.value,
            base_version_id=base_version_id,
            status=WorkflowStatus.RUNNING.value,
            current_stage=WorkflowStage.SAVING_VERSION.value,
        )
        stale_edit = WorkflowRunModel(
            routebook_id=routebook_id,
            run_type=WorkflowRunType.EDIT.value,
            base_version_id=base_version_id,
            status=WorkflowStatus.RUNNING.value,
            current_stage=WorkflowStage.SAVING_VERSION.value,
        )
        session.add_all([first_edit, stale_edit])
        session.flush()
        first_edit_id = first_edit.id
        stale_edit_id = stale_edit.id

    with SessionFactory.begin() as session:
        VersionService.commit(
            session,
            routebook_id=routebook_id,
            workflow_run_id=first_edit_id,
            base_version_id=base_version_id,
            snapshot=RouteBookSnapshotV1(notes=["first edit"]),
            change_type=ChangeType.EDIT,
            change_summary="first",
        )

    with pytest.raises(VersionConflictError), SessionFactory.begin() as session:
        VersionService.commit(
            session,
            routebook_id=routebook_id,
            workflow_run_id=stale_edit_id,
            base_version_id=base_version_id,
            snapshot=RouteBookSnapshotV1(notes=["stale edit"]),
            change_type=ChangeType.EDIT,
            change_summary="stale",
        )


def test_requirement_message_api_is_idempotent_and_dispatches_safe_retry() -> None:
    routebook_id = uuid4()
    foundation_run_id = uuid4()
    with SessionFactory.begin() as session:
        session.add(RouteBookModel(id=routebook_id, title="南京需求对话", status="draft"))
        session.add(
            WorkflowRunModel(
                id=foundation_run_id,
                routebook_id=routebook_id,
                run_type=WorkflowRunType.CREATE.value,
                status=WorkflowStatus.COMPLETED.value,
                current_stage=WorkflowStage.COMPLETED.value,
            )
        )
    dispatched: list[tuple[UUID, UUID, bool]] = []
    app = create_app(
        lambda _run_id, _request_id: None,
        lambda run_id, message_id, _request_id, resume: dispatched.append(
            (run_id, message_id, resume)
        ),
    )

    with TestClient(app) as client:
        payload = {"message_id": "client-message-001", "text": "从上海去南京玩三天"}
        first = client.post(f"/api/routebooks/{routebook_id}/messages", json=payload)
        retried = client.post(f"/api/routebooks/{routebook_id}/messages", json=payload)
        conflict = client.post(
            f"/api/routebooks/{routebook_id}/messages",
            json={**payload, "text": "改去杭州"},
        )
        history = client.get(f"/api/routebooks/{routebook_id}/messages")

    assert first.status_code == 202
    assert retried.status_code == 202
    assert retried.json()["reused"] is True
    assert retried.json()["workflow_run_id"] == first.json()["workflow_run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(dispatched) == 2
    assert dispatched[0] == dispatched[1]
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_requirement_resume_requires_interrupted_run_and_is_idempotent() -> None:
    routebook_id = uuid4()
    run_id = uuid4()
    with SessionFactory.begin() as session:
        session.add(RouteBookModel(id=routebook_id, title="武汉需求对话", status="draft"))
        session.add(
            WorkflowRunModel(
                id=run_id,
                routebook_id=routebook_id,
                run_type=WorkflowRunType.CREATE.value,
                status=WorkflowStatus.RUNNING.value,
                current_stage=WorkflowStage.EXTRACTING_REQUIREMENTS.value,
            )
        )
    dispatched: list[tuple[UUID, UUID, bool]] = []
    app = create_app(
        lambda _run_id, _request_id: None,
        lambda workflow_id, message_id, _request_id, resume: dispatched.append(
            (workflow_id, message_id, resume)
        ),
    )
    payload = {
        "interrupt_kind": "requirement_clarification",
        "message_id": "client-message-101",
        "text": "9月1日从上海出发，自驾",
    }

    with TestClient(app) as client:
        invalid = client.post(f"/api/workflow-runs/{run_id}/resume", json=payload)
        with SessionFactory.begin() as session:
            WorkflowService.mark_interrupted(session, run_id)
        first = client.post(f"/api/workflow-runs/{run_id}/resume", json=payload)
        retried = client.post(f"/api/workflow-runs/{run_id}/resume", json=payload)

    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "WORKFLOW_STATE_CONFLICT"
    assert first.status_code == 202
    assert first.json()["workflow_status"] == "queued"
    assert retried.status_code == 202
    assert retried.json()["reused"] is True
    assert dispatched[0] == dispatched[1]
    assert dispatched[0][2] is True
    with SessionFactory() as session:
        assert session.scalar(
            select(func.count())
            .select_from(ConversationMessageModel)
            .where(ConversationMessageModel.workflow_run_id == run_id)
        ) == 1


def test_requirement_worker_interrupts_resumes_and_commits_one_version(monkeypatch) -> None:
    initial_run_id = _create_routebook_and_run()
    setup_checkpointer()
    execute_foundation_workflow.run(str(initial_run_id), "integration-request")
    with SessionFactory.begin() as session:
        initial_run = session.get(WorkflowRunModel, initial_run_id)
        assert initial_run is not None
        routebook_id = initial_run.routebook_id
        first = RequirementMessageService.start(
            session,
            routebook_id=routebook_id,
            client_message_id="worker-message-001",
            text="去南京玩三天",
        )

    patches = {
        "去南京玩三天": RequirementPatch(
            destination=_explicit("南京"),
            days=_explicit(3),
        ),
        "9月1日从上海出发，自驾": RequirementPatch(
            origin=_explicit("上海"),
            start_date=_explicit(date(2026, 9, 1)),
            transport_mode=_explicit("driving"),
        ),
    }
    extractor = _IntegrationExtractor(patches)
    monkeypatch.setattr(
        "services.api.app.worker.AnthropicRequirementExtractor",
        lambda _settings: extractor,
    )

    execute_requirement_workflow.run(
        str(first.workflow_run_id), str(first.message.id), "integration-request", False
    )
    with SessionFactory.begin() as session:
        interrupted = session.get(WorkflowRunModel, first.workflow_run_id)
        assert interrupted is not None
        assert interrupted.status == WorkflowStatus.INTERRUPTED.value
        assert interrupted.current_stage == WorkflowStage.WAITING_FOR_CLARIFICATION.value
        resumed = RequirementMessageService.resume(
            session,
            run_id=first.workflow_run_id,
            client_message_id="worker-message-002",
            text="9月1日从上海出发，自驾",
        )

    execute_requirement_workflow.run(
        str(first.workflow_run_id), str(resumed.message.id), "integration-request", True
    )
    execute_requirement_workflow.run(
        str(first.workflow_run_id), str(resumed.message.id), "integration-request", True
    )

    with SessionFactory() as session:
        run = session.get(WorkflowRunModel, first.workflow_run_id)
        assert run is not None and run.result_version_id is not None
        assert run.status == WorkflowStatus.COMPLETED.value
        version = session.get(RouteBookVersionModel, run.result_version_id)
        assert version is not None
        snapshot = RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)
        assert snapshot.requirements.origin.value == "上海"
        assert snapshot.requirements.destination.value == "南京"
        assert snapshot.requirements.start_date.value == date(2026, 9, 1)
        assert snapshot.requirements.days.value == 3
        assert snapshot.requirements.transport_mode.value == "driving"
        assert session.scalar(
            select(func.count())
            .select_from(RouteBookVersionModel)
            .where(RouteBookVersionModel.workflow_run_id == first.workflow_run_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(LlmCallRecordModel)
            .where(LlmCallRecordModel.workflow_run_id == first.workflow_run_id)
        ) == 2
        assert session.scalar(
            select(func.count())
            .select_from(ConversationMessageModel)
            .where(ConversationMessageModel.workflow_run_id == first.workflow_run_id)
        ) == 3
        system_messages = list(
            session.scalars(
                select(ConversationMessageModel).where(
                    ConversationMessageModel.workflow_run_id == first.workflow_run_id,
                    ConversationMessageModel.role == "assistant",
                )
            )
        )
        assert len(system_messages) == 1
        assert len(system_messages[0].payload_jsonb["questions"]) <= 3


class _IntegrationExtractor:
    def __init__(self, patches: dict[str, RequirementPatch]) -> None:
        self.patches = patches

    def extract(self, message, current, *, today):
        return ExtractionResult(
            patch=self.patches[message],
            trace=ExtractionTrace(
                prompt_version="requirement-extraction-v1",
                model="integration-fake-model",
                response_id=f"response-{len(message)}",
                attempt_count=1,
                latency_ms=1,
                recorded_at=datetime.now(UTC),
            ),
        )


def _explicit(value):
    return RequirementPatchValue(value=value, source="explicit", confidence=0.99)
def _create_routebook_and_run() -> UUID:
    routebook_id = uuid4()
    run_id = uuid4()
    with SessionFactory.begin() as session:
        session.add(RouteBookModel(id=routebook_id, title="集成测试路书", status="draft"))
        session.add(
            WorkflowRunModel(
                id=run_id,
                routebook_id=routebook_id,
                run_type=WorkflowRunType.CREATE.value,
                status=WorkflowStatus.QUEUED.value,
                current_stage=WorkflowStage.QUEUED.value,
            )
        )
    return run_id
