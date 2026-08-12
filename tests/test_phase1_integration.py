from __future__ import annotations

import os
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
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)
from services.api.app.schemas import RouteBookSnapshotV1
from services.api.app.services import VersionService
from services.api.app.worker import execute_foundation_workflow

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_database():
    with SessionFactory.begin() as session:
        session.execute(
            text(
                "TRUNCATE routebook.idempotency_records, "
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
