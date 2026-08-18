from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..errors import NotFoundError, WorkflowStateConflictError
from ..models import PlaceProposalModel, RecommendationBatchModel, utc_now
from ..repositories import RecommendationRepository, RouteBookRepository, VersionRepository
from ..schemas import (
    RecommendationBatchRead,
    RecommendationCandidateRead,
    RecommendationObservabilityRead,
    RouteBookSnapshotV1,
)
from ..requirements.models import RequirementPatch
from ..requirements.service import RequirementService
from .models import PlaceFeedback, PlaceProposalStatus, RecommendationResult


class RecommendationPersistenceService:
    @staticmethod
    def save(
        session: Session,
        *,
        routebook_id: UUID,
        base_version_id: UUID,
        result: RecommendationResult,
    ) -> RecommendationBatchModel:
        routebook = RouteBookRepository(session).get(routebook_id, for_update=True)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id != base_version_id:
            raise WorkflowStateConflictError(
                details={"reason": "recommendation_base_version_changed"}
            )
        repository = RecommendationRepository(session)
        batch = RecommendationBatchModel(
            routebook_id=routebook_id,
            base_version_id=base_version_id,
            strategy_jsonb=result.strategy.model_dump(mode="json"),
            metrics_jsonb=result.metrics.model_dump(mode="json"),
        )
        repository.add_batch(batch)
        session.flush()
        for proposal in result.proposals:
            repository.add_proposal(
                PlaceProposalModel(
                    batch_id=batch.id,
                    routebook_id=routebook_id,
                    provider_place_id=proposal.candidate.provider_place_id,
                    candidate_jsonb=proposal.candidate.model_dump(mode="json"),
                    reason=proposal.reason,
                    tradeoffs_jsonb=proposal.tradeoffs,
                    evidence_jsonb=proposal.evidence.model_dump(mode="json"),
                    status=proposal.status.value,
                )
            )
        session.flush()
        return batch

    @staticmethod
    def apply_feedback(
        session: Session,
        *,
        routebook_id: UUID,
        proposal_id: UUID,
        feedback: PlaceFeedback,
    ) -> PlaceProposalModel:
        proposal = RecommendationRepository(session).get_proposal(
            routebook_id, proposal_id, for_update=True
        )
        if proposal is None:
            raise NotFoundError(details={"resource": "place_proposal"})
        expected = {
            "accept": PlaceProposalStatus.ACCEPTED,
            "reject": PlaceProposalStatus.REJECTED,
            "replace": PlaceProposalStatus.REPLACED,
        }[feedback.action]
        if proposal.status != PlaceProposalStatus.PROPOSED.value:
            if proposal.status == expected.value:
                return proposal
            raise WorkflowStateConflictError(
                details={"status": proposal.status, "expected": "proposed"}
            )
        proposal.status = expected.value
        proposal.feedback_reason = feedback.reason.value if feedback.reason else None
        proposal.feedback_note = feedback.note
        proposal.resolved_at = utc_now()
        session.flush()
        return proposal

    @staticmethod
    def latest(session: Session, routebook_id: UUID) -> RecommendationBatchRead:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        repository = RecommendationRepository(session)
        batch = repository.latest_batch(routebook_id)
        if batch is None:
            raise NotFoundError(details={"resource": "recommendation_batch"})
        return present_batch(batch, repository.list_proposals(batch.id))

    @staticmethod
    def confirmed_requirements(
        session: Session, routebook_id: UUID
    ) -> tuple[UUID, RouteBookSnapshotV1]:
        routebook = RouteBookRepository(session).get(routebook_id)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id is None:
            raise WorkflowStateConflictError(details={"reason": "requirements_not_confirmed"})
        version = VersionRepository(session).get(routebook.current_version_id)
        if version is None:
            raise NotFoundError(details={"resource": "routebook_version"})
        snapshot = RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)
        decision = RequirementService().apply(snapshot.requirements, RequirementPatch())
        if not decision.ready:
            raise WorkflowStateConflictError(
                details={
                    "reason": "requirements_not_confirmed",
                    "blocking_issues": [item.code for item in decision.blocking_issues],
                }
            )
        return version.id, snapshot

    @staticmethod
    def rejected_reasons(session: Session, routebook_id: UUID) -> list[str]:
        return RecommendationRepository(session).rejected_reasons(routebook_id)

    @staticmethod
    def feedback_history(session: Session, routebook_id: UUID) -> list[PlaceFeedback]:
        proposals = RecommendationRepository(session).list_for_routebook(routebook_id)
        feedback: list[PlaceFeedback] = []
        for proposal in proposals:
            action = {
                "accepted": "accept",
                "rejected": "reject",
                "replaced": "replace",
            }.get(proposal.status)
            if action is None:
                continue
            feedback.append(
                PlaceFeedback(
                    provider_place_id=proposal.provider_place_id,
                    action=action,
                    reason=proposal.feedback_reason,
                    note=proposal.feedback_note,
                )
            )
        return feedback

    @staticmethod
    def observability(
        session: Session, routebook_id: UUID
    ) -> RecommendationObservabilityRead:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        proposals = RecommendationRepository(session).list_for_routebook(routebook_id)
        counts = {status: 0 for status in ("proposed", "accepted", "rejected", "replaced")}
        reasons: dict[str, int] = {}
        auto_adopted = 0
        for proposal in proposals:
            counts[proposal.status] += 1
            if "auto_adopted" in proposal.evidence_jsonb.get("signals", []):
                auto_adopted += 1
            if proposal.feedback_reason:
                reasons[proposal.feedback_reason] = reasons.get(proposal.feedback_reason, 0) + 1
        resolved = counts["accepted"] + counts["rejected"] + counts["replaced"]
        accepted_rate = counts["accepted"] / resolved if resolved else 0.0
        correction_rate = (
            (counts["rejected"] + counts["replaced"]) / resolved if resolved else 0.0
        )
        return RecommendationObservabilityRead(
            proposed_count=counts["proposed"],
            accepted_count=counts["accepted"],
            rejected_count=counts["rejected"],
            replaced_count=counts["replaced"],
            auto_adopted_count=auto_adopted,
            recommendation_acceptance_rate=round(accepted_rate, 4),
            user_correction_rate=round(correction_rate, 4),
            rejection_reason_distribution=reasons,
        )


def present_batch(
    batch: RecommendationBatchModel, proposals: list[PlaceProposalModel]
) -> RecommendationBatchRead:
    candidates: list[RecommendationCandidateRead] = []
    for proposal in proposals:
        candidate = proposal.candidate_jsonb
        evidence = proposal.evidence_jsonb
        candidates.append(
            RecommendationCandidateRead(
                id=proposal.id,
                provider_place_id=proposal.provider_place_id,
                name=str(candidate["name"]),
                type=str(candidate["category_normalized"]),
                address=str(candidate.get("address", "")),
                district=str(candidate.get("district", "")),
                recommendation_reason=proposal.reason,
                transport_tradeoffs=proposal.tradeoffs_jsonb,
                score=float(evidence["final_score"]),
                score_evidence=[str(item) for item in evidence.get("signals", [])],
                status=proposal.status,
            )
        )
    return RecommendationBatchRead(
        id=batch.id,
        routebook_id=batch.routebook_id,
        base_version_id=batch.base_version_id,
        strategy=batch.strategy_jsonb,
        metrics=batch.metrics_jsonb,
        candidates=candidates,
        created_at=batch.created_at,
    )
