from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ChangeProposalModel,
    IdempotencyRecordModel,
    RouteBookModel,
    RouteBookVersionModel,
    WorkflowRunModel,
)


class RouteBookRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, routebook: RouteBookModel) -> None:
        self.session.add(routebook)

    def get(self, routebook_id: UUID, *, for_update: bool = False) -> RouteBookModel | None:
        statement = select(RouteBookModel).where(RouteBookModel.id == routebook_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)


class WorkflowRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, workflow_run: WorkflowRunModel) -> None:
        self.session.add(workflow_run)

    def get(self, run_id: UUID, *, for_update: bool = False) -> WorkflowRunModel | None:
        statement = select(WorkflowRunModel).where(WorkflowRunModel.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)


class VersionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, version: RouteBookVersionModel) -> None:
        self.session.add(version)

    def get(self, version_id: UUID) -> RouteBookVersionModel | None:
        return self.session.get(RouteBookVersionModel, version_id)

    def get_by_workflow_run(self, run_id: UUID) -> RouteBookVersionModel | None:
        return self.session.scalar(
            select(RouteBookVersionModel).where(RouteBookVersionModel.workflow_run_id == run_id)
        )


class ProposalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, proposal: ChangeProposalModel) -> None:
        self.session.add(proposal)

    def get(self, proposal_id: UUID, *, for_update: bool = False) -> ChangeProposalModel | None:
        statement = select(ChangeProposalModel).where(ChangeProposalModel.id == proposal_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: IdempotencyRecordModel) -> None:
        self.session.add(record)

    def get(self, scope: str, key: str) -> IdempotencyRecordModel | None:
        return self.session.scalar(
            select(IdempotencyRecordModel).where(
                IdempotencyRecordModel.scope == scope,
                IdempotencyRecordModel.key == key,
            )
        )
