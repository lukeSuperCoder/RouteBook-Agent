from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from ..enums import (
    ChangeType,
    RequirementSource,
    RouteBookStatus,
    WorkflowRunType,
    WorkflowStage,
    WorkflowStatus,
)
from ..errors import NotFoundError, WorkflowStateConflictError
from ..models import PlaceProposalModel, WorkflowRunModel
from ..providers.models import PlaceCandidate
from ..recommendations.models import PlaceProposalStatus
from ..repositories import RecommendationRepository, RouteBookRepository, WorkflowRunRepository
from ..schemas import (
    ItineraryDaySnapshot,
    PlaceSnapshot,
    RequirementValue,
    RouteBookSnapshotV1,
    RouteSegmentSnapshot,
    WeatherSnapshot,
)
from ..services import VersionService
from .models import PlanningPlace, PlanningResult
from .service import planning_place_id


def select_must_visit_match(
    text: str, proposals: list[PlaceProposalModel]
) -> PlaceProposalModel | None:
    exact = [
        proposal
        for proposal in proposals
        if text == str(proposal.candidate_jsonb.get("name", ""))
    ]
    matches = exact or [
        proposal
        for proposal in proposals
        if text in str(proposal.candidate_jsonb.get("name", ""))
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda proposal: (
            -float(proposal.evidence_jsonb.get("final_score", 0)),
            len(str(proposal.candidate_jsonb.get("name", ""))),
            proposal.provider_place_id,
        ),
    )


class PlanningPersistenceService:
    @staticmethod
    def load_input(
        session: Session, routebook_id: UUID
    ) -> tuple[UUID, RouteBookSnapshotV1, list[PlanningPlace]]:
        routebook = RouteBookRepository(session).get(routebook_id)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        if routebook.current_version_id is None:
            raise WorkflowStateConflictError(details={"reason": "requirements_not_confirmed"})
        from ..repositories import VersionRepository

        version = VersionRepository(session).get(routebook.current_version_id)
        if version is None:
            raise NotFoundError(details={"resource": "routebook_version"})
        snapshot = RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)
        accepted = [
            proposal
            for proposal in RecommendationRepository(session).list_for_routebook(routebook_id)
            if proposal.status == PlaceProposalStatus.ACCEPTED.value
            or "auto_adopted" in proposal.evidence_jsonb.get("signals", [])
        ]
        by_provider_id = {item.provider_place_id: item for item in accepted}
        must_ids = set(snapshot.requirements.must_visit_place_ids.value or [])
        unresolved_must_texts: list[str] = []
        for text in snapshot.requirements.must_visit_place_texts.value or []:
            match = select_must_visit_match(text, list(by_provider_id.values()))
            if match is not None:
                candidate = PlaceCandidate.model_validate(match.candidate_jsonb)
                must_ids.add(planning_place_id(candidate.provider, candidate.provider_place_id))
            else:
                unresolved_must_texts.append(text)
        original_must_ids = set(snapshot.requirements.must_visit_place_ids.value or [])
        original_must_texts = snapshot.requirements.must_visit_place_texts.value or []
        if must_ids != original_must_ids or unresolved_must_texts != original_must_texts:
            requirements = snapshot.requirements.model_copy(
                update={
                    "must_visit_place_ids": RequirementValue(
                        value=sorted(must_ids, key=str),
                        source=RequirementSource.EXPLICIT,
                        confidence=1.0,
                        confirmed=True,
                    ),
                    "must_visit_place_texts": RequirementValue(
                        value=unresolved_must_texts,
                        source=RequirementSource.EXPLICIT,
                        confidence=1.0,
                        confirmed=True,
                    ),
                }
            )
            snapshot = snapshot.model_copy(update={"requirements": requirements})
        excluded_ids = set(snapshot.requirements.excluded_place_ids.value or [])
        places: list[PlanningPlace] = []
        for proposal in by_provider_id.values():
            candidate = PlaceCandidate.model_validate(proposal.candidate_jsonb)
            place_id = planning_place_id(candidate.provider, candidate.provider_place_id)
            if place_id in excluded_ids:
                raise WorkflowStateConflictError(
                    details={
                        "reason": "excluded_place_present",
                        "place_id": str(place_id),
                    }
                )
            priority = (
                "must_visit"
                if place_id in must_ids
                else (
                    "auto_adopted"
                    if "auto_adopted" in proposal.evidence_jsonb.get("signals", [])
                    else "accepted"
                )
            )
            places.append(PlanningPlace(id=place_id, candidate=candidate, priority=priority))
        return version.id, snapshot, places

    @staticmethod
    def commit(
        session: Session,
        *,
        routebook_id: UUID,
        base_version_id: UUID,
        base_snapshot: RouteBookSnapshotV1,
        result: PlanningResult,
        workflow_run_id: UUID | None = None,
    ) -> UUID:
        if not result.feasible or result.draft is None:
            raise WorkflowStateConflictError(
                details={
                    "reason": "itinerary_infeasible",
                    "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
                }
            )
        routebook = RouteBookRepository(session).get(routebook_id, for_update=True)
        if routebook is None:
            raise NotFoundError(details={"resource": "routebook"})
        run = (
            WorkflowRunRepository(session).get(workflow_run_id, for_update=True)
            if workflow_run_id else None
        )
        if run is None:
            run = WorkflowRunModel(
                routebook_id=routebook_id,
                run_type=WorkflowRunType.CREATE.value,
                base_version_id=base_version_id,
                status=WorkflowStatus.RUNNING.value,
                current_stage=WorkflowStage.VALIDATING.value,
            )
            WorkflowRunRepository(session).add(run)
            session.flush()
        draft = result.draft
        all_places = [place for day in draft.days for place in day.places]
        snapshot = base_snapshot.model_copy(
            update={
                "places": [
                    PlaceSnapshot(
                        id=place.id,
                        provider=place.candidate.provider,
                        provider_place_id=place.candidate.provider_place_id,
                        name=place.candidate.name,
                        address=place.candidate.address,
                        district=place.candidate.district,
                        longitude=place.candidate.coordinate.longitude,
                        latitude=place.candidate.coordinate.latitude,
                        category_raw=place.candidate.category_raw,
                        category_normalized=place.candidate.category_normalized.value,
                        semantic_type=place.candidate.semantic_type.value,
                        status=place.candidate.status,
                    )
                    for place in all_places
                ],
                "days_plan": [
                    ItineraryDaySnapshot(
                        day_number=day.day_number,
                        date=day.date,
                        place_ids=[place.id for place in day.places],
                        segment_ids=[segment.id for segment in day.segments],
                        weather_refs=[
                            fact.ref
                            for fact in draft.weather
                            if fact.place_id in {place.id for place in day.places}
                        ],
                        notes=day.notes,
                    )
                    for day in draft.days
                ],
                "route_segments": [
                    RouteSegmentSnapshot.model_validate(segment.model_dump(mode="python"))
                    for day in draft.days
                    for segment in day.segments
                ],
                "weather": [
                    WeatherSnapshot.model_validate(fact.model_dump(mode="python"))
                    for fact in draft.weather
                ],
                "warnings": draft.warnings,
            }
        )
        version = VersionService.commit(
            session,
            routebook_id=routebook_id,
            workflow_run_id=run.id,
            base_version_id=base_version_id,
            snapshot=snapshot,
            change_type=ChangeType.EDIT,
            change_summary="生成分日行程、正式路线与天气草稿",
        )
        routebook.status = RouteBookStatus.EDITABLE.value
        run.result_version_id = version.id
        session.flush()
        return version.id
