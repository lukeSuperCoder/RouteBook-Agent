from uuid import uuid4

from services.api.app.enums import FactStatus, RequirementSource
from services.api.app.finalization import validate_final_snapshot
from services.api.app.schemas import (
    ItineraryDaySnapshot,
    PlaceSnapshot,
    RequirementSnapshot,
    RequirementValue,
    RouteBookSnapshotV1,
)


def _place(*, status: FactStatus = FactStatus.VERIFIED) -> PlaceSnapshot:
    return PlaceSnapshot(
        id=uuid4(),
        provider="amap",
        provider_place_id="poi-1",
        name="中山陵",
        longitude=118.85,
        latitude=32.06,
        status=status,
    )


def test_complete_snapshot_passes_finalization() -> None:
    place = _place()
    snapshot = RouteBookSnapshotV1(
        requirements=RequirementSnapshot(
            days=RequirementValue(
                value=1,
                source=RequirementSource.EXPLICIT,
                confidence=1,
                confirmed=True,
            ),
            must_visit_place_ids=RequirementValue(
                value=[place.id],
                source=RequirementSource.EXPLICIT,
                confidence=1,
                confirmed=True,
            ),
        ),
        places=[place],
        days_plan=[ItineraryDaySnapshot(day_number=1, place_ids=[place.id])],
    )

    assert validate_final_snapshot(snapshot) == []


def test_finalization_reports_all_blocking_integrity_issues() -> None:
    proposed = _place(status=FactStatus.PROPOSED)
    missing = uuid4()
    excluded = proposed.id
    snapshot = RouteBookSnapshotV1(
        requirements=RequirementSnapshot(
            days=RequirementValue(value=2),
            must_visit_place_ids=RequirementValue(value=[missing]),
            excluded_place_ids=RequirementValue(value=[excluded]),
        ),
        places=[proposed],
        days_plan=[
            ItineraryDaySnapshot(
                day_number=1,
                place_ids=[proposed.id, uuid4()],
                segment_ids=[uuid4()],
            )
        ],
    )

    issues = validate_final_snapshot(snapshot)

    assert {item.code for item in issues} == {
        "DAY_COUNT_MISMATCH",
        "DANGLING_PLACE",
        "DANGLING_SEGMENT",
        "MUST_VISIT_MISSING",
        "EXCLUDED_PLACE_INCLUDED",
        "PROPOSED_FACTS",
    }
