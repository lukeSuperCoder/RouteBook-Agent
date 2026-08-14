from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ChangeProposalModel,
    ConversationMessageModel,
    FinalPageModel,
    IdempotencyRecordModel,
    LlmCallRecordModel,
    PlaceProposalModel,
    RecommendationBatchModel,
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

    def list_for_routebook(self, routebook_id: UUID) -> list[RouteBookVersionModel]:
        return list(
            self.session.scalars(
                select(RouteBookVersionModel)
                .where(RouteBookVersionModel.routebook_id == routebook_id)
                .order_by(RouteBookVersionModel.version_number.desc())
            )
        )


class FinalPageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, final_page: FinalPageModel) -> None:
        self.session.add(final_page)

    def get_by_token_hash(self, token_hash: str) -> FinalPageModel | None:
        return self.session.scalar(
            select(FinalPageModel).where(
                FinalPageModel.public_token_hash == token_hash,
                FinalPageModel.revoked_at.is_(None),
            )
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

    def list_for_routebook(self, routebook_id: UUID) -> list[ChangeProposalModel]:
        return list(
            self.session.scalars(
                select(ChangeProposalModel)
                .where(ChangeProposalModel.routebook_id == routebook_id)
                .order_by(ChangeProposalModel.created_at, ChangeProposalModel.id)
            )
        )


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


class ConversationMessageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, message: ConversationMessageModel) -> None:
        self.session.add(message)

    def get(self, message_id: UUID) -> ConversationMessageModel | None:
        return self.session.get(ConversationMessageModel, message_id)

    def get_by_client_id(
        self, routebook_id: UUID, client_message_id: str
    ) -> ConversationMessageModel | None:
        return self.session.scalar(
            select(ConversationMessageModel).where(
                ConversationMessageModel.routebook_id == routebook_id,
                ConversationMessageModel.client_message_id == client_message_id,
            )
        )

    def latest_for_workflow(self, run_id: UUID) -> ConversationMessageModel | None:
        return self.session.scalar(
            select(ConversationMessageModel)
            .where(ConversationMessageModel.workflow_run_id == run_id)
            .order_by(
                ConversationMessageModel.created_at.desc(),
                ConversationMessageModel.id.desc(),
            )
            .limit(1)
        )

    def list_for_routebook(self, routebook_id: UUID) -> list[ConversationMessageModel]:
        return list(
            self.session.scalars(
                select(ConversationMessageModel)
                .where(ConversationMessageModel.routebook_id == routebook_id)
                .order_by(ConversationMessageModel.created_at, ConversationMessageModel.id)
            )
        )


class LlmCallRecordRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: LlmCallRecordModel) -> None:
        self.session.add(record)

    def exists(self, run_id: UUID, message_id: UUID, attempt_count: int) -> bool:
        return (
            self.session.scalar(
                select(LlmCallRecordModel.id).where(
                    LlmCallRecordModel.workflow_run_id == run_id,
                    LlmCallRecordModel.message_id == message_id,
                    LlmCallRecordModel.attempt_count == attempt_count,
                )
            )
            is not None
        )


class RecommendationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_batch(self, batch: RecommendationBatchModel) -> None:
        self.session.add(batch)

    def add_proposal(self, proposal: PlaceProposalModel) -> None:
        self.session.add(proposal)

    def latest_batch(self, routebook_id: UUID) -> RecommendationBatchModel | None:
        return self.session.scalar(
            select(RecommendationBatchModel)
            .where(RecommendationBatchModel.routebook_id == routebook_id)
            .order_by(
                RecommendationBatchModel.created_at.desc(),
                RecommendationBatchModel.id.desc(),
            )
            .limit(1)
        )

    def list_proposals(self, batch_id: UUID) -> list[PlaceProposalModel]:
        return list(
            self.session.scalars(
                select(PlaceProposalModel)
                .where(PlaceProposalModel.batch_id == batch_id)
                .order_by(PlaceProposalModel.created_at, PlaceProposalModel.id)
            )
        )

    def get_proposal(
        self, routebook_id: UUID, proposal_id: UUID, *, for_update: bool = False
    ) -> PlaceProposalModel | None:
        statement = select(PlaceProposalModel).where(
            PlaceProposalModel.id == proposal_id,
            PlaceProposalModel.routebook_id == routebook_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def rejected_reasons(self, routebook_id: UUID) -> list[str]:
        return [
            reason
            for reason in self.session.scalars(
                select(PlaceProposalModel.feedback_reason)
                .where(
                    PlaceProposalModel.routebook_id == routebook_id,
                    PlaceProposalModel.status.in_(("rejected", "replaced")),
                    PlaceProposalModel.feedback_reason.is_not(None),
                )
                .distinct()
            )
            if reason is not None
        ]

    def list_for_routebook(self, routebook_id: UUID) -> list[PlaceProposalModel]:
        return list(
            self.session.scalars(
                select(PlaceProposalModel).where(PlaceProposalModel.routebook_id == routebook_id)
            )
        )
