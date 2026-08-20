from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from services.api.app.editing.graph import invoke_editing_subgraph
from services.api.app.editing.models import EditIntent
from services.api.app.editing.recompute import AffectedScopeRecomputer
from services.api.app.editing.service import EditingService, day_hash
from services.api.app.main import _editing_intent
from services.api.app.enums import FactStatus, RequirementSource
from services.api.app.providers.models import (
    Coordinate,
    DailyForecast,
    FactCollection,
    RouteResult,
    PlaceCandidate,
)
from services.api.app.schemas import (
    ItineraryDaySnapshot,
    PlaceSnapshot,
    RequirementSnapshot,
    RequirementValue,
    RouteBookSnapshotV1,
    RouteSegmentSnapshot,
    WeatherSnapshot,
    RouteBookEditRequest,
)


def uid(value: int) -> UUID:
    return UUID(int=value)


def place(value: int, name: str, district: str = "玄武区") -> PlaceSnapshot:
    return PlaceSnapshot(
        id=uid(value),
        provider="amap",
        provider_place_id=f"p{value}",
        name=name,
        address=f"南京市{district}",
        district=district,
        longitude=118.8,
        latitude=32.0,
        category_normalized="attraction",
        semantic_type="attraction",
        status=FactStatus.VERIFIED,
    )


def snapshot() -> RouteBookSnapshotV1:
    places = [place(1, "南京博物院"), place(2, "中山陵"), place(3, "玄武湖")]
    segments = [
        RouteSegmentSnapshot(
            id=uid(101),
            origin_place_id=uid(1),
            destination_place_id=uid(2),
            mode="driving",
            distance_meters=5_000,
            duration_seconds=900,
            provider="amap",
            status=FactStatus.VERIFIED,
        )
    ]
    return RouteBookSnapshotV1(
        requirements=RequirementSnapshot(
            days=RequirementValue(
                value=2,
                source=RequirementSource.EXPLICIT,
                confidence=1,
                confirmed=True,
            ),
            must_visit_place_ids=RequirementValue(
                value=[uid(1)],
                source=RequirementSource.EXPLICIT,
                confidence=1,
                confirmed=True,
            ),
        ),
        places=places,
        days_plan=[
            ItineraryDaySnapshot(
                day_number=1,
                date=date(2026, 10, 1),
                place_ids=[uid(1), uid(2)],
                segment_ids=[uid(101)],
                weather_refs=["w1"],
            ),
            ItineraryDaySnapshot(
                day_number=2,
                date=date(2026, 10, 2),
                place_ids=[uid(3)],
                weather_refs=["w2"],
            ),
        ],
        route_segments=segments,
        weather=[
            WeatherSnapshot(ref="w1", place_id=uid(1), status=FactStatus.VERIFIED),
            WeatherSnapshot(ref="w2", place_id=uid(3), status=FactStatus.VERIFIED),
        ],
    )


def test_resolves_chinese_day_and_preserves_other_day_hash() -> None:
    base = snapshot()
    plan = EditingService().plan(
        base,
        EditIntent(operation="edit_day", day_reference="第二天", note="下午慢一点"),
    )

    assert plan.resolution.resolved is True
    assert plan.impact.affected_days == [2]
    assert plan.preview is not None
    assert plan.preview.days_plan[1].notes == []
    assert plan.preview.days_plan[1].segment_ids == []
    assert plan.preview.days_plan[1].weather_refs == []
    assert day_hash(base, base.days_plan[0]) == day_hash(
        plan.preview, plan.preview.days_plan[0]
    )
    assert EditingService.validate_unchanged_days(base, plan.preview, [2]) is True


def test_ambiguous_place_reference_returns_clarification() -> None:
    base = snapshot().model_copy(
        update={"places": [place(1, "城墙遗址"), place(2, "明城墙遗址"), place(3, "玄武湖")]}
    )

    plan = EditingService().plan(
        base, EditIntent(operation="remove_place", place_reference="城墙")
    )

    assert plan.resolution.resolved is False
    assert plan.preview is None
    assert len(plan.resolution.candidates) == 2


def test_removing_must_visit_requires_confirmation() -> None:
    plan = EditingService().plan(
        snapshot(), EditIntent(operation="remove_place", place_reference="南京博物院")
    )

    assert plan.impact.requires_confirmation is True
    assert {risk.code for risk in plan.risks} == {"remove_must_visit"}
    assert plan.preview is not None
    assert uid(1) not in plan.preview.days_plan[0].place_ids


def test_replacing_place_invalidates_only_affected_routes_and_weather() -> None:
    base = snapshot()
    replacement = place(4, "夫子庙", "秦淮区")
    plan = EditingService().plan(
        base,
        EditIntent(
            operation="replace_place",
            place_reference="中山陵",
            replacement_place=replacement,
        ),
    )

    assert plan.preview is not None
    assert uid(2) not in {item.id for item in plan.preview.places}
    assert uid(4) in plan.preview.days_plan[0].place_ids
    assert plan.preview.days_plan[0].segment_ids == []
    assert plan.preview.days_plan[1] == base.days_plan[1]
    assert plan.preview.weather == [base.weather[1]]


def test_change_total_days_creates_major_multi_day_proposal() -> None:
    plan = EditingService().plan(
        snapshot(), EditIntent(operation="change_days", target_days=3)
    )

    assert plan.preview is not None
    assert len(plan.preview.days_plan) == 3
    assert plan.preview.requirements.days.value == 3
    assert plan.impact.requires_confirmation is True
    assert plan.impact.major_route_changed is True
    assert {risk.code for risk in plan.risks} == {
        "change_total_days",
        "major_route_change",
        "multi_day_change",
    }


def test_unchanged_day_validation_detects_unexpected_scope_change() -> None:
    base = snapshot()
    corrupted = base.model_copy(
        update={
            "days_plan": [
                base.days_plan[0].model_copy(update={"notes": ["unexpected"]}),
                base.days_plan[1].model_copy(update={"notes": ["intended"]}),
            ]
        }
    )

    assert EditingService.validate_unchanged_days(base, corrupted, [2]) is False


def test_recompute_queries_only_affected_day_and_preserves_other_day_hash() -> None:
    base = snapshot()
    plan = EditingService().plan(
        base,
        EditIntent(
            operation="replace_place",
            place_reference="中山陵",
            replacement_place=place(4, "夫子庙"),
        ),
    )
    assert plan.preview is not None
    calls: list[str] = []
    now = datetime.now(UTC)

    def route(origin: Coordinate, destination: Coordinate, mode: str) -> RouteResult:
        calls.append(f"route:{mode}")
        return RouteResult(
            mode="driving",
            origin=origin,
            destination=destination,
            distance_meters=3_000,
            duration_seconds=600,
            fetched_at=now,
        )

    def weather(location: Coordinate) -> FactCollection[DailyForecast]:
        calls.append("weather")
        return FactCollection(
            status=FactStatus.VERIFIED,
            items=[
                DailyForecast(
                    location=location,
                    forecast_date=date(2026, 10, 1),
                    temp_min_c=18,
                    temp_max_c=25,
                    text_day="晴",
                    text_night="晴",
                    wind_scale_day="2",
                    provider_updated_at=now,
                    fetched_at=now,
                )
            ],
        )

    recomputed = AffectedScopeRecomputer(route, weather).recompute(
        plan.preview, plan.impact
    )

    assert calls == ["route:driving", "weather"]
    assert recomputed.route_segments[0].distance_meters == 3_000
    assert recomputed.route_segments[0].provider == "amap"
    assert day_hash(base, base.days_plan[1]) == day_hash(
        recomputed, recomputed.days_plan[1]
    )


def test_edit_day_replans_only_target_day_routes_and_weather() -> None:
    base = snapshot()
    plan = EditingService().plan(
        base,
        EditIntent(operation="edit_day", day_reference="第一天", note="下午轻松一点"),
    )
    assert plan.preview is not None
    calls: list[str] = []
    now = datetime.now(UTC)

    def route(origin: Coordinate, destination: Coordinate, mode: str) -> RouteResult:
        calls.append(f"route:{mode}")
        return RouteResult(
            mode=mode,
            origin=origin,
            destination=destination,
            distance_meters=2_000,
            duration_seconds=480,
            fetched_at=now,
        )

    def weather(location: Coordinate) -> FactCollection[DailyForecast]:
        calls.append("weather")
        return FactCollection(status=FactStatus.UNAVAILABLE, items=[])

    recomputed = AffectedScopeRecomputer(route, weather).recompute(plan.preview, plan.impact)

    assert calls == ["route:driving", "weather"]
    assert recomputed.days_plan[0].segment_ids
    assert recomputed.route_segments[0].distance_meters == 2_000
    assert day_hash(base, base.days_plan[1]) == day_hash(recomputed, recomputed.days_plan[1])


def test_editing_subgraph_returns_serializable_strict_plan() -> None:
    plan = invoke_editing_subgraph(
        snapshot(),
        EditIntent(operation="edit_day", day_reference="第一天", note="早点出发"),
    )

    assert plan.resolution.resolved is True
    assert plan.preview is not None
    assert plan.preview.days_plan[0].notes == []
    assert plan.preview.days_plan[0].segment_ids == []


def test_natural_add_place_edit_resolves_provider_place() -> None:
    base = snapshot().model_copy(
        update={
            "requirements": snapshot().requirements.model_copy(
                update={
                    "destination": RequirementValue(
                        value="南京",
                        source=RequirementSource.EXPLICIT,
                        confidence=1,
                        confirmed=True,
                    )
                }
            )
        }
    )
    seen: list[tuple[str, str]] = []

    def resolve(keyword: str, region: str) -> PlaceCandidate:
        seen.append((keyword, region))
        return PlaceCandidate(
            provider_place_id="tiananmen",
            name="天安门",
            city="南京市",
            district="玄武区",
            coordinate=Coordinate(longitude=118.79, latitude=32.04),
            fetched_at=datetime.now(UTC),
        )

    intent = _editing_intent(
        RouteBookEditRequest(
            operation_id=uid(999),
            operation="edit_day",
            day_reference="第2天",
            note="增加天安门",
        ),
        base,
        resolve,
    )

    assert seen == [("天安门", "南京")]
    assert intent.operation == "add_place"
    assert intent.day_reference == "第2天"
    assert intent.replacement_place is not None
    assert intent.replacement_place.name == "天安门"
