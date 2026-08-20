from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from ..enums import FactStatus
from ..errors import ProviderError
from ..providers.models import (
    Coordinate,
    DailyForecast,
    FactCollection,
    RouteResult,
    WeatherWarning,
)
from ..schemas import RequirementSnapshot
from ..weather_policy import classify_forecast, forecast_for_date
from .models import (
    CAPACITY_TEMPLATES,
    ItineraryDraft,
    PlannedDay,
    PlannedSegment,
    PlanningConflict,
    PlanningPlace,
    PlanningResult,
    WeatherFact,
)
from .optimizer import limited_two_opt, nearest_neighbor

RouteFetcher = Callable[[Coordinate, Coordinate, str], RouteResult]
WeatherFetcher = Callable[[Coordinate], FactCollection[DailyForecast]]
WarningFetcher = Callable[[Coordinate], FactCollection[WeatherWarning]]


class ItineraryPlanningService:
    def __init__(
        self,
        *,
        route_fetcher: RouteFetcher,
        weather_fetcher: WeatherFetcher,
        warning_fetcher: WarningFetcher,
    ) -> None:
        self._route_fetcher = route_fetcher
        self._weather_fetcher = weather_fetcher
        self._warning_fetcher = warning_fetcher

    def plan(
        self, requirements: RequirementSnapshot, places: list[PlanningPlace]
    ) -> PlanningResult:
        preflight = self._preflight(requirements, places)
        if preflight:
            return PlanningResult(feasible=False, conflicts=preflight)
        days = int(requirements.days.value or 0)
        intensity = requirements.intensity.value or "moderate"
        capacity = CAPACITY_TEMPLATES[intensity]
        forecasts = self._planning_forecasts(requirements, places)
        arranged = self._arrange(
            places, days, capacity.maximum_places, requirements=requirements, forecasts=forecasts
        )
        attempts = 0
        while attempts <= 3:
            with ThreadPoolExecutor(max_workers=min(7, len(arranged) or 1)) as executor:
                planned = list(
                    executor.map(
                        lambda item: self._route_day(
                            item[0] + 1, requirements, item[1], capacity, forecasts
                        ),
                        enumerate(arranged),
                    )
                )
            conflicts = self._validate(planned, places, requirements)
            repairable = [item for item in conflicts if item.repairable]
            blocking = [item for item in conflicts if not item.repairable]
            if not conflicts:
                weather, warnings, degraded = self._fetch_weather(planned)
                return PlanningResult(
                    feasible=True,
                    draft=ItineraryDraft(
                        days=planned,
                        weather=weather,
                        warnings=warnings,
                        repair_attempts=attempts,
                        degraded=degraded
                        or any(
                            segment.status != FactStatus.VERIFIED
                            for day in planned
                            for segment in day.segments
                        ),
                    ),
                )
            if blocking or not repairable or attempts == 3:
                if attempts == 3 and repairable:
                    conflicts.append(
                        PlanningConflict(
                            code="repair_exhausted",
                            message="行程在三轮修复后仍不可行。",
                            repairable=False,
                        )
                    )
                return PlanningResult(feasible=False, conflicts=conflicts)
            arranged = self._repair(arranged, repairable)
            attempts += 1
        raise AssertionError("bounded repair loop escaped")

    @staticmethod
    def _preflight(
        requirements: RequirementSnapshot, places: list[PlanningPlace]
    ) -> list[PlanningConflict]:
        conflicts: list[PlanningConflict] = []
        if requirements.must_visit_place_texts.value:
            conflicts.append(
                PlanningConflict(
                    code="unresolved_must_visit",
                    message="仍有必去地点文本尚未解析为已确认 POI。",
                    repairable=False,
                )
            )
        days = requirements.days.value or 0
        if not 1 <= days <= 7 or not 3 <= len(places) <= 15:
            conflicts.append(
                PlanningConflict(
                    code="place_count_out_of_range",
                    message="阶段五要求 1～7 天且包含 3～15 个已确认地点。",
                    repairable=False,
                )
            )
        invalid = [place.id for place in places if not place.candidate.provider_place_id]
        if invalid:
            conflicts.append(
                PlanningConflict(
                    code="invalid_place_fact",
                    message="规划地点缺少经验证的供应商 POI ID。",
                    place_ids=invalid,
                    repairable=False,
                )
            )
        scheduled_ids = {place.id for place in places}
        missing_must_ids = [
            place_id
            for place_id in (requirements.must_visit_place_ids.value or [])
            if place_id not in scheduled_ids
        ]
        if missing_must_ids:
            conflicts.append(
                PlanningConflict(
                    code="must_visit_missing",
                    message="已解析的必去地点未包含在可规划地点中。",
                    place_ids=missing_must_ids,
                    repairable=False,
                )
            )
        excluded = scheduled_ids.intersection(requirements.excluded_place_ids.value or [])
        if excluded:
            conflicts.append(
                PlanningConflict(
                    code="excluded_place_present",
                    message="排除地点不得进入行程规划。",
                    place_ids=sorted(excluded, key=str),
                    repairable=False,
                )
            )
        return conflicts

    @staticmethod
    def _arrange(
        places: list[PlanningPlace], days: int, maximum_places: int, *,
        requirements: RequirementSnapshot | None = None,
        forecasts: list[DailyForecast] | None = None,
    ) -> list[list[PlanningPlace]]:
        grouped: defaultdict[str, list[PlanningPlace]] = defaultdict(list)
        for place in sorted(places, key=lambda item: item.priority != "must_visit"):
            grouped[f"{place.candidate.city}:{place.candidate.district}"].append(place)
        result: list[list[PlanningPlace]] = [[] for _ in range(days)]
        day_index = 0
        for group in grouped.values():
            for place in group:
                category = place.candidate.category_normalized.value
                candidates = [i for i, day in enumerate(result) if len(day) < maximum_places]
                if not candidates:
                    result[day_index % days].append(place)
                    day_index += 1
                    continue
                def day_score(
                    index: int, place_category: str = category
                ) -> tuple[int, int, int]:
                    forecast = forecast_for_date(
                        forecasts or [],
                        requirements.start_date.value + timedelta(days=index)
                        if requirements and requirements.start_date.value else None,
                    )
                    preference = classify_forecast(forecast) if forecast else None
                    weather_penalty = 0
                    if preference and preference.preferred_categories:
                        weather_penalty = (
                            0 if place_category in preference.preferred_categories else 1
                        )
                    return weather_penalty, len(result[index]), index

                target = min(candidates, key=day_score)
                result[target].append(place)
        return result

    def _planning_forecasts(
        self, requirements: RequirementSnapshot, places: list[PlanningPlace]
    ) -> list[DailyForecast]:
        if not places or requirements.start_date.value is None:
            return []
        try:
            return self._weather_fetcher(places[0].candidate.coordinate).items
        except ProviderError:
            return []

    def _route_day(
        self,
        number: int,
        requirements: RequirementSnapshot,
        places: list[PlanningPlace],
        capacity: object,
        forecasts: list[DailyForecast] | None = None,
    ) -> PlannedDay:
        ordered = limited_two_opt(nearest_neighbor(places))
        mode = requirements.transport_mode.value or "driving"
        segments: list[PlannedSegment] = []
        for left, right in zip(ordered, ordered[1:], strict=False):
            try:
                route = self._route_fetcher(
                    left.candidate.coordinate, right.candidate.coordinate, mode
                )
                segments.append(
                    PlannedSegment(
                        id=uuid4(),
                        origin_place_id=left.id,
                        destination_place_id=right.id,
                        mode=mode,
                        distance_meters=route.distance_meters,
                        duration_seconds=route.duration_seconds,
                        provider=route.provider,
                        queried_at=route.fetched_at,
                        status=route.status,
                    )
                )
            except ProviderError:
                segments.append(
                    PlannedSegment(
                        id=uuid4(),
                        origin_place_id=left.id,
                        destination_place_id=right.id,
                        mode=mode,
                        status=FactStatus.UNVERIFIED,
                    )
                )
        start = requirements.start_date.value
        from .models import DailyCapacity

        forecast = forecast_for_date(
            forecasts or [],
            start + timedelta(days=number - 1) if start is not None else None,
        )
        notes = [f"天气安排：{classify_forecast(forecast).summary}"] if forecast else []
        return PlannedDay(
            day_number=number,
            date=start + timedelta(days=number - 1) if start is not None else None,
            places=ordered,
            segments=segments,
            capacity=DailyCapacity.model_validate(capacity),
            notes=notes,
        )

    @staticmethod
    def _validate(
        days: list[PlannedDay],
        source: list[PlanningPlace],
        requirements: RequirementSnapshot,
    ) -> list[PlanningConflict]:
        conflicts: list[PlanningConflict] = []
        scheduled = {place.id for day in days for place in day.places}
        missing = [
            place.id
            for place in source
            if place.priority == "must_visit" and place.id not in scheduled
        ]
        if missing:
            conflicts.append(
                PlanningConflict(
                    code="must_visit_missing",
                    message="必去地点未全部进入行程。",
                    place_ids=missing,
                    repairable=False,
                )
            )
        for day in days:
            transport = sum((segment.duration_seconds or 0) for segment in day.segments) // 60
            activity = sum(place.estimated_activity_minutes for place in day.places)
            if (
                len(day.places) > day.capacity.maximum_places
                or activity > day.capacity.maximum_activity_minutes
            ):
                conflicts.append(
                    PlanningConflict(
                        code="capacity_exceeded",
                        message="每日地点或活动时长超过容量。",
                        day_number=day.day_number,
                        place_ids=[item.id for item in day.places],
                        repairable=any(item.priority == "accepted" for item in day.places),
                    )
                )
            if transport > day.capacity.maximum_transport_minutes or any(
                (segment.duration_seconds or 0) // 60 > day.capacity.maximum_segment_minutes
                for segment in day.segments
            ):
                conflicts.append(
                    PlanningConflict(
                        code="route_limit_exceeded",
                        message="正式路线时长超过每日交通容量。",
                        day_number=day.day_number,
                        place_ids=[item.id for item in day.places],
                        repairable=any(item.priority == "accepted" for item in day.places),
                    )
                )
            districts = {
                place.candidate.district for place in day.places if place.candidate.district
            }
            route_distance = sum(segment.distance_meters or 0 for segment in day.segments)
            repairable = any(item.priority == "accepted" for item in day.places)
            if len(districts) >= 3 and route_distance > 60_000:
                conflicts.append(
                    PlanningConflict(
                        code="cross_district_excess",
                        message="同日跨越多个行政区且正式路线距离过长。",
                        day_number=day.day_number,
                        place_ids=[item.id for item in day.places],
                        repairable=repairable,
                    )
                )
            if (
                requirements.suburban_acceptance.value is False
                and len(districts) > 1
                and any((segment.distance_meters or 0) > 30_000 for segment in day.segments)
            ):
                conflicts.append(
                    PlanningConflict(
                        code="suburban_mixed",
                        message="远郊地点与市区地点存在不合理同日混排。",
                        day_number=day.day_number,
                        place_ids=[item.id for item in day.places],
                        repairable=repairable,
                    )
                )
            categories = [place.candidate.category_normalized.value for place in day.places]
            if len(categories) >= 4 and len(set(categories)) == 1:
                conflicts.append(
                    PlanningConflict(
                        code="category_repetition",
                        message="同日地点类型过度重复。",
                        day_number=day.day_number,
                        place_ids=[item.id for item in day.places],
                        repairable=repairable,
                    )
                )
        return conflicts

    @staticmethod
    def _repair(
        arranged: list[list[PlanningPlace]], conflicts: list[PlanningConflict]
    ) -> list[list[PlanningPlace]]:
        repaired = [day.copy() for day in arranged]
        for conflict in conflicts:
            if conflict.day_number is None:
                continue
            day = repaired[conflict.day_number - 1]
            removable = next((item for item in reversed(day) if item.priority == "accepted"), None)
            if removable is not None:
                day.remove(removable)
        return repaired

    def _fetch_weather(
        self, days: list[PlannedDay]
    ) -> tuple[list[WeatherFact], list[dict[str, object]], bool]:
        anchors = [(day, day.places[0]) for day in days if day.places and day.date is not None]
        weather: list[WeatherFact] = []
        warnings: list[dict[str, object]] = []
        degraded = False

        def fetch(
            item: tuple[PlannedDay, PlanningPlace],
        ) -> tuple[
            PlannedDay,
            PlanningPlace,
            FactCollection[DailyForecast] | None,
            FactCollection[WeatherWarning] | None,
        ]:
            day, place = item
            try:
                return (
                    day,
                    place,
                    self._weather_fetcher(place.candidate.coordinate),
                    self._warning_fetcher(place.candidate.coordinate),
                )
            except ProviderError:
                return day, place, None, None

        with ThreadPoolExecutor(max_workers=min(8, len(anchors) * 2 or 1)) as executor:
            results = list(executor.map(fetch, anchors))
        for day, place, forecasts, alerts in results:
            forecast = next(
                (
                    item
                    for item in (forecasts.items if forecasts else [])
                    if item.forecast_date == day.date
                ),
                None,
            )
            if forecast is None:
                degraded = True
                weather.append(
                    WeatherFact(
                        ref=f"weather:{place.id}:{day.date}",
                        place_id=place.id,
                        status=FactStatus.UNAVAILABLE,
                    )
                )
            else:
                weather.append(
                    WeatherFact(
                        ref=f"weather:{place.id}:{day.date}",
                        place_id=place.id,
                        forecast_at=None,
                        provider_updated_at=forecast.provider_updated_at,
                        queried_at=forecast.fetched_at,
                        status=forecast.status,
                        payload=forecast.model_dump(mode="json"),
                    )
                )
            for alert in alerts.items if alerts else []:
                warnings.append({"place_id": str(place.id), **alert.model_dump(mode="json")})
        return weather, warnings, degraded


def planning_place_id(provider: str, provider_place_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"routebook:{provider}:{provider_place_id}")
