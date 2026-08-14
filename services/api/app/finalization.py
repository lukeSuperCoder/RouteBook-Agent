from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from .errors import NotFoundError, ValidationAppError, VersionConflictError
from .models import FinalPageModel
from .repositories import FinalPageRepository, RouteBookRepository, VersionRepository
from .schemas import FinalizationIssue, RouteBookSnapshotV1, SharedRouteBookRead


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_final_snapshot(snapshot: RouteBookSnapshotV1) -> list[FinalizationIssue]:
    issues: list[FinalizationIssue] = []
    place_ids = {item.id for item in snapshot.places}
    segment_ids = {item.id for item in snapshot.route_segments}
    planned_place_ids = {place_id for day in snapshot.days_plan for place_id in day.place_ids}

    if not snapshot.days_plan:
        issues.append(
            FinalizationIssue(code="EMPTY_ITINERARY", message="最终路书至少需要一天行程。")
        )
    expected_days = snapshot.requirements.days.value
    if expected_days is not None and len(snapshot.days_plan) != expected_days:
        issues.append(
            FinalizationIssue(
                code="DAY_COUNT_MISMATCH",
                message="分日行程数量与确认天数不一致。",
                details={"expected": expected_days, "actual": len(snapshot.days_plan)},
            )
        )
    dangling_places = sorted(str(item) for item in planned_place_ids - place_ids)
    dangling_segments = sorted(
        str(segment_id)
        for day in snapshot.days_plan
        for segment_id in day.segment_ids
        if segment_id not in segment_ids
    )
    if dangling_places:
        issues.append(
            FinalizationIssue(
                code="DANGLING_PLACE",
                message="行程引用了不存在的地点。",
                details={"place_ids": dangling_places},
            )
        )
    if dangling_segments:
        issues.append(
            FinalizationIssue(
                code="DANGLING_SEGMENT",
                message="行程引用了不存在的路线段。",
                details={"segment_ids": dangling_segments},
            )
        )
    must_visit = set(snapshot.requirements.must_visit_place_ids.value or [])
    missing_must_visit = sorted(str(item) for item in must_visit - planned_place_ids)
    if missing_must_visit:
        issues.append(
            FinalizationIssue(
                code="MUST_VISIT_MISSING",
                message="最终行程遗漏了必去地点。",
                details={"place_ids": missing_must_visit},
            )
        )
    excluded = set(snapshot.requirements.excluded_place_ids.value or [])
    included_excluded = sorted(str(item) for item in excluded & planned_place_ids)
    if included_excluded:
        issues.append(
            FinalizationIssue(
                code="EXCLUDED_PLACE_INCLUDED",
                message="最终行程包含已排除地点。",
                details={"place_ids": included_excluded},
            )
        )
    proposed = sorted(str(item.id) for item in snapshot.places if item.status == "proposed")
    if proposed:
        issues.append(
            FinalizationIssue(
                code="PROPOSED_FACTS",
                message="提案地点不能进入最终页面。",
                details={"place_ids": proposed},
            )
        )
    return issues


@dataclass(frozen=True)
class FinalizationResult:
    final_page: FinalPageModel
    public_token: str


class FinalizationService:
    @staticmethod
    def finalize(
        session: Session, *, routebook_id: UUID, version_id: UUID, privacy_policy: str
    ) -> FinalizationResult:
        routebook = RouteBookRepository(session).get(routebook_id, for_update=True)
        version = VersionRepository(session).get(version_id)
        if routebook is None or version is None or version.routebook_id != routebook_id:
            raise NotFoundError(details={"resource": "routebook_version"})
        if routebook.current_version_id != version_id:
            raise VersionConflictError(
                "只能将当前正式版本生成为最终页面。",
                details={"current_version_id": str(routebook.current_version_id)},
            )
        snapshot = RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)
        issues = validate_final_snapshot(snapshot)
        if issues:
            raise ValidationAppError(
                "当前版本未通过最终完整性检查。",
                details={"issues": [item.model_dump(mode="json") for item in issues]},
            )
        public_token = secrets.token_urlsafe(24)
        final_page = FinalPageModel(
            routebook_id=routebook_id,
            routebook_version_id=version_id,
            public_token_hash=token_hash(public_token),
            privacy_policy=privacy_policy,
            created_at=datetime.now(UTC),
        )
        FinalPageRepository(session).add(final_page)
        routebook.latest_final_version_id = version_id
        routebook.status = "final"
        session.flush()
        return FinalizationResult(final_page=final_page, public_token=public_token)

    @staticmethod
    def load_shared(session: Session, public_token: str) -> SharedRouteBookRead:
        final_page = FinalPageRepository(session).get_by_token_hash(token_hash(public_token))
        if final_page is None:
            raise NotFoundError(details={"resource": "shared_routebook"})
        routebook = RouteBookRepository(session).get(final_page.routebook_id)
        version = VersionRepository(session).get(final_page.routebook_version_id)
        if routebook is None or version is None:
            raise NotFoundError(details={"resource": "shared_routebook"})
        snapshot = RouteBookSnapshotV1.model_validate(version.snapshot_jsonb)
        if final_page.privacy_policy == "redact_addresses":
            snapshot = snapshot.model_copy(
                update={
                    "places": [item.model_copy(update={"address": ""}) for item in snapshot.places]
                }
            )
        return SharedRouteBookRead(
            title=routebook.title,
            routebook_version_id=version.id,
            version_number=version.version_number,
            snapshot=snapshot,
            privacy_policy=final_page.privacy_policy,
            created_at=final_page.created_at,
        )
