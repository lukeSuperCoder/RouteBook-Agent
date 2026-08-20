from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from services.api.app.enums import FactStatus, RequirementSource
from services.api.app.errors import ProviderUnavailableError
from services.api.app.planning.graph import invoke_itinerary_planning_subgraph
from services.api.app.planning.models import CAPACITY_TEMPLATES, PlanningPlace
from services.api.app.planning.optimizer import limited_two_opt, route_length
from services.api.app.planning.persistence import select_must_visit_match
from services.api.app.planning.service import ItineraryPlanningService, planning_place_id
from services.api.app.providers.models import (
    Coordinate,
    DailyForecast,
    FactCollection,
    NormalizedPlaceCategory,
    PlaceCandidate,
    PlaceSemanticType,
    RouteResult,
    WeatherWarning,
)
from services.api.app.schemas import RequirementSnapshot, RequirementValue


def place(
    place_id: str,
    name: str,
    longitude: float,
    latitude: float,
    *,
    district: str = "玄武区",
    priority: str = "accepted",
    category: NormalizedPlaceCategory = NormalizedPlaceCategory.ATTRACTION,
) -> PlanningPlace:
    candidate = PlaceCandidate(
        provider_place_id=place_id,
        name=name,
        address=f"南京市{district}",
        province="江苏省",
        city="南京市",
        district=district,
        adcode="320102",
        coordinate=Coordinate(longitude=longitude, latitude=latitude),
        category_raw="风景名胜",
        category_normalized=category,
        semantic_type=PlaceSemanticType.ATTRACTION,
        fetched_at=datetime.now(UTC),
    )
    return PlanningPlace(
        id=planning_place_id("amap", place_id),
        candidate=candidate,
        priority=priority,
    )


def requirements(*, days: int = 2, intensity: str = "moderate") -> RequirementSnapshot:
    explicit = RequirementSource.EXPLICIT
    return RequirementSnapshot(
        destination=RequirementValue(value="南京", source=explicit, confidence=1, confirmed=True),
        start_date=RequirementValue(
            value=date(2026, 10, 1), source=explicit, confidence=1, confirmed=True
        ),
        days=RequirementValue(value=days, source=explicit, confidence=1, confirmed=True),
        transport_mode=RequirementValue(
            value="driving", source=explicit, confidence=1, confirmed=True
        ),
        intensity=RequirementValue(value=intensity, source=explicit, confidence=1, confirmed=True),
    )


def route_fetcher(origin: Coordinate, destination: Coordinate, mode: str) -> RouteResult:
    assert mode in {
        "driving", "walking", "public_transit", "taxi", "cycling", "mixed", "system_decides"
    }
    return RouteResult(
        mode="driving",
        origin=origin,
        destination=destination,
        distance_meters=8_000,
        duration_seconds=1_200,
        fetched_at=datetime.now(UTC),
    )


def weather_fetcher(location: Coordinate) -> FactCollection[DailyForecast]:
    now = datetime.now(UTC)
    return FactCollection(
        status=FactStatus.VERIFIED,
        items=[
            DailyForecast(
                location=location,
                forecast_date=date(2026, 10, day),
                temp_min_c=18,
                temp_max_c=26,
                text_day="晴",
                text_night="多云",
                wind_scale_day="2",
                provider_updated_at=now,
                fetched_at=now,
            )
            for day in (1, 2, 3)
        ],
    )


def warning_fetcher(location: Coordinate) -> FactCollection[WeatherWarning]:
    return FactCollection(status=FactStatus.VERIFIED, items=[])


def service() -> ItineraryPlanningService:
    return ItineraryPlanningService(
        route_fetcher=route_fetcher,
        weather_fetcher=weather_fetcher,
        warning_fetcher=warning_fetcher,
    )


def test_daily_capacity_templates_are_ordered_by_intensity() -> None:
    assert CAPACITY_TEMPLATES["relaxed"].maximum_places == 2
    assert CAPACITY_TEMPLATES["moderate"].maximum_places == 3
    assert CAPACITY_TEMPLATES["compact"].maximum_places == 4
    assert (
        CAPACITY_TEMPLATES["relaxed"].maximum_transport_minutes
        < CAPACITY_TEMPLATES["compact"].maximum_transport_minutes
    )


def test_two_opt_never_increases_approximate_route_length() -> None:
    places = [
        place("a", "A", 118.70, 32.00),
        place("b", "B", 118.90, 32.20),
        place("c", "C", 118.70, 32.20),
        place("d", "D", 118.90, 32.00),
    ]

    optimized = limited_two_opt(places)

    assert route_length(optimized) <= route_length(places)


def test_plans_continuous_days_and_uses_only_provider_route_facts() -> None:
    places = [
        place("a", "南京博物院", 118.83, 32.04, priority="must_visit"),
        place("b", "中山陵", 118.85, 32.06),
        place("c", "玄武湖", 118.79, 32.08),
        place("d", "总统府", 118.80, 32.04),
    ]

    result = service().plan(requirements(), places)

    assert result.feasible is True
    assert result.draft is not None
    assert [day.date for day in result.draft.days] == [
        date(2026, 10, 1),
        date(2026, 10, 2),
    ]
    assert {item.id for day in result.draft.days for item in day.places} == {
        item.id for item in places
    }
    assert all(
        segment.provider == "amap"
        and segment.distance_meters == 8_000
        and segment.duration_seconds == 1_200
        for day in result.draft.days
        for segment in day.segments
    )


def test_weather_moves_indoor_places_to_rainy_day_and_outdoor_places_to_sunny_day() -> None:
    now = datetime.now(UTC)

    def mixed_weather(location: Coordinate) -> FactCollection[DailyForecast]:
        return FactCollection(
            status=FactStatus.VERIFIED,
            items=[
                DailyForecast(
                    location=location, forecast_date=date(2026, 10, 1), temp_min_c=16,
                    temp_max_c=20, text_day="中雨", text_night="小雨", wind_scale_day="3",
                    provider_updated_at=now, fetched_at=now,
                ),
                DailyForecast(
                    location=location, forecast_date=date(2026, 10, 2), temp_min_c=18,
                    temp_max_c=27, text_day="晴", text_night="晴", wind_scale_day="2",
                    provider_updated_at=now, fetched_at=now,
                ),
            ],
        )

    planner = ItineraryPlanningService(
        route_fetcher=route_fetcher,
        weather_fetcher=mixed_weather,
        warning_fetcher=warning_fetcher,
    )
    places = [
        place("museum", "南京博物院", 118.83, 32.04, category=NormalizedPlaceCategory.MUSEUM),
        place("shopping", "室内街区", 118.82, 32.04, category=NormalizedPlaceCategory.SHOPPING),
        place("park", "玄武湖", 118.79, 32.08, category=NormalizedPlaceCategory.PARK),
        place("landmark", "中山陵", 118.85, 32.06, category=NormalizedPlaceCategory.LANDMARK),
    ]

    result = planner.plan(requirements(), places)

    assert result.draft is not None
    assert {item.candidate.category_normalized for item in result.draft.days[0].places} == {
        NormalizedPlaceCategory.MUSEUM, NormalizedPlaceCategory.SHOPPING
    }
    assert {item.candidate.category_normalized for item in result.draft.days[1].places} == {
        NormalizedPlaceCategory.PARK, NormalizedPlaceCategory.LANDMARK
    }
    assert "优先安排室内活动" in result.draft.days[0].notes[0]
    assert "适合安排户外活动" in result.draft.days[1].notes[0]


def test_unresolved_must_visit_returns_structured_conflict() -> None:
    req = requirements().model_copy(
        update={
            "must_visit_place_texts": RequirementValue(
                value=["南京城墙"],
                source=RequirementSource.EXPLICIT,
                confidence=1,
                confirmed=True,
            )
        }
    )

    result = service().plan(
        req,
        [
            place("a", "A", 118.7, 32.0),
            place("b", "B", 118.8, 32.0),
            place("c", "C", 118.9, 32.0),
        ],
    )

    assert result.feasible is False
    assert result.conflicts[0].code == "unresolved_must_visit"
    assert result.conflicts[0].repairable is False


def test_route_and_weather_failures_keep_places_and_mark_degraded() -> None:
    def unavailable_route(_origin: Coordinate, _destination: Coordinate, _mode: str) -> RouteResult:
        raise ProviderUnavailableError(details={"provider": "amap"})

    def unavailable_weather(_location: Coordinate) -> FactCollection[DailyForecast]:
        raise ProviderUnavailableError(details={"provider": "qweather"})

    planner = ItineraryPlanningService(
        route_fetcher=unavailable_route,
        weather_fetcher=unavailable_weather,
        warning_fetcher=warning_fetcher,
    )
    places = [
        place("a", "A", 118.7, 32.0),
        place("b", "B", 118.8, 32.0),
        place("c", "C", 118.9, 32.0),
    ]

    result = planner.plan(requirements(days=1, intensity="compact"), places)

    assert result.feasible is True
    assert result.draft is not None and result.draft.degraded is True
    assert len(result.draft.days[0].places) == 3
    assert all(segment.status == FactStatus.UNVERIFIED for segment in result.draft.days[0].segments)
    assert result.draft.weather[0].status == FactStatus.UNAVAILABLE


def test_flexible_date_and_public_transit_still_computes_route_facts() -> None:
    req = requirements(days=1, intensity="compact").model_copy(
        update={
            "start_date": RequirementValue(),
            "date_precision": RequirementValue(
                value="flexible", source=RequirementSource.EXPLICIT, confidence=1, confirmed=True
            ),
            "transport_mode": RequirementValue(
                value="public_transit", source=RequirementSource.EXPLICIT, confidence=1, confirmed=True
            ),
        }
    )
    places = [
        place("a", "A", 118.7, 32.0),
        place("b", "B", 118.8, 32.0),
        place("c", "C", 118.9, 32.0),
    ]

    result = service().plan(req, places)

    assert result.feasible is True
    assert result.draft is not None
    assert result.draft.days[0].date is None
    assert result.draft.weather == []
    assert all(
        segment.mode == "public_transit"
        and segment.status == FactStatus.VERIFIED
        and segment.distance_meters == 8_000
        and segment.duration_seconds == 1_200
        for segment in result.draft.days[0].segments
    )


def test_out_of_range_place_count_is_not_silently_planned() -> None:
    result = service().plan(
        requirements(days=1),
        [place("a", "A", 118.7, 32.0), place("b", "B", 118.8, 32.0)],
    )

    assert result.feasible is False
    assert result.conflicts[0].code == "place_count_out_of_range"


def test_must_visit_match_prefers_best_scored_selected_place_when_names_overlap() -> None:
    class Proposal:
        def __init__(self, place_id: str, name: str, score: float) -> None:
            self.provider_place_id = place_id
            self.candidate_jsonb = {"name": name}
            self.evidence_jsonb = {"final_score": score}

    proposals = [
        Proposal("music", "钟山风景名胜区中山陵景区音乐台", 0.7),
        Proposal("main", "中山陵景区", 0.87),
        Proposal("hall", "中山陵祭堂", 0.7),
    ]

    selected = select_must_visit_match("中山陵", proposals)  # type: ignore[arg-type]

    assert selected is not None
    assert selected.provider_place_id == "main"


def test_bounded_repair_removes_only_low_priority_places() -> None:
    must = place("must", "必去", 118.70, 32.00, priority="must_visit")
    optional = [
        place(f"optional-{index}", f"推荐{index}", 118.75 + index * 0.01, 32.00)
        for index in range(3)
    ]

    result = service().plan(requirements(days=1, intensity="relaxed"), [must, *optional])

    assert result.feasible is True
    assert result.draft is not None
    assert result.draft.repair_attempts == 1
    assert must.id in {item.id for item in result.draft.days[0].places}
    assert len(result.draft.days[0].places) == 2


def test_impossible_must_visit_capacity_returns_conflict_without_deletion() -> None:
    places = [
        place(f"must-{index}", f"必去{index}", 118.7 + index * 0.01, 32.0, priority="must_visit")
        for index in range(3)
    ]

    result = service().plan(requirements(days=1, intensity="relaxed"), places)

    assert result.feasible is False
    assert result.conflicts[0].code == "capacity_exceeded"
    assert result.conflicts[0].repairable is False


@pytest.mark.parametrize("days", range(1, 8))
def test_one_to_seven_day_matrix_generates_continuous_itinerary(days: int) -> None:
    count = min(15, max(3, days * 2))
    places = [
        place(
            f"p{index}",
            f"地点{index}",
            118.70 + index * 0.005,
            32.00 + index * 0.004,
            district=f"区域{index // 3}",
            priority="must_visit" if index == 0 else "accepted",
        )
        for index in range(count)
    ]

    result = service().plan(requirements(days=days), places)

    assert result.feasible is True
    assert result.draft is not None
    assert [day.day_number for day in result.draft.days] == list(range(1, days + 1))
    assert [day.date for day in result.draft.days] == [
        date(2026, 10, 1) + timedelta(days=index) for index in range(days)
    ]


def test_itinerary_planning_subgraph_returns_strict_result() -> None:
    places = [
        place("a", "A", 118.70, 32.00),
        place("b", "B", 118.75, 32.00),
        place("c", "C", 118.80, 32.00),
    ]

    result = invoke_itinerary_planning_subgraph(
        service(), requirements(days=1, intensity="compact"), places
    )

    assert result.feasible is True
    assert result.draft is not None
    assert len(result.draft.days[0].segments) == 2
