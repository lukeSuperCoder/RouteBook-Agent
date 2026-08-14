from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from ..enums import FactStatus
from ..errors import ProviderError
from ..providers.models import Coordinate, DailyForecast, FactCollection, RouteResult
from ..schemas import (
    RouteBookSnapshotV1,
    RouteSegmentSnapshot,
    WeatherSnapshot,
)
from .models import ImpactScope

RouteFetcher = Callable[[Coordinate, Coordinate, str], RouteResult]
WeatherFetcher = Callable[[Coordinate], FactCollection[DailyForecast]]


class AffectedScopeRecomputer:
    def __init__(self, route_fetcher: RouteFetcher, weather_fetcher: WeatherFetcher) -> None:
        self._route_fetcher = route_fetcher
        self._weather_fetcher = weather_fetcher

    def recompute(
        self, snapshot: RouteBookSnapshotV1, impact: ImpactScope
    ) -> RouteBookSnapshotV1:
        affected = set(impact.affected_days)
        place_by_id = {place.id: place for place in snapshot.places}
        preserved_segments = [
            segment
            for segment in snapshot.route_segments
            if all(
                segment.id not in day.segment_ids
                for day in snapshot.days_plan
                if day.day_number in affected
            )
        ]
        preserved_weather = [
            fact
            for fact in snapshot.weather
            if all(
                fact.ref not in day.weather_refs
                for day in snapshot.days_plan
                if day.day_number in affected
            )
        ]
        new_segments: list[RouteSegmentSnapshot] = []
        new_weather: list[WeatherSnapshot] = []
        updated_days = []
        mode = snapshot.requirements.transport_mode.value or "driving"
        for day in snapshot.days_plan:
            if day.day_number not in affected:
                updated_days.append(day)
                continue
            segment_ids = []
            for origin_id, destination_id in zip(
                day.place_ids, day.place_ids[1:], strict=False
            ):
                origin = place_by_id[origin_id]
                destination = place_by_id[destination_id]
                segment_id = uuid4()
                try:
                    result = self._route_fetcher(
                        Coordinate(longitude=origin.longitude, latitude=origin.latitude),
                        Coordinate(
                            longitude=destination.longitude,
                            latitude=destination.latitude,
                        ),
                        mode,
                    )
                    segment = RouteSegmentSnapshot(
                        id=segment_id,
                        origin_place_id=origin_id,
                        destination_place_id=destination_id,
                        mode=mode,
                        distance_meters=result.distance_meters,
                        duration_seconds=result.duration_seconds,
                        provider=result.provider,
                        queried_at=result.fetched_at,
                        status=result.status,
                    )
                except ProviderError:
                    segment = RouteSegmentSnapshot(
                        id=segment_id,
                        origin_place_id=origin_id,
                        destination_place_id=destination_id,
                        mode=mode,
                        status=FactStatus.UNVERIFIED,
                    )
                new_segments.append(segment)
                segment_ids.append(segment_id)
            weather_refs: list[str] = []
            if day.place_ids and day.date:
                anchor = place_by_id[day.place_ids[0]]
                ref = f"weather:{anchor.id}:{day.date}"
                try:
                    forecasts = self._weather_fetcher(
                        Coordinate(longitude=anchor.longitude, latitude=anchor.latitude)
                    )
                    forecast = next(
                        (item for item in forecasts.items if item.forecast_date == day.date),
                        None,
                    )
                except ProviderError:
                    forecast = None
                if forecast is None:
                    fact = WeatherSnapshot(
                        ref=ref,
                        place_id=anchor.id,
                        status=FactStatus.UNAVAILABLE,
                    )
                else:
                    fact = WeatherSnapshot(
                        ref=ref,
                        place_id=anchor.id,
                        provider_updated_at=forecast.provider_updated_at,
                        queried_at=forecast.fetched_at,
                        status=forecast.status,
                        payload=forecast.model_dump(mode="json"),
                    )
                new_weather.append(fact)
                weather_refs.append(ref)
            updated_days.append(
                day.model_copy(
                    update={
                        "segment_ids": segment_ids,
                        "weather_refs": weather_refs,
                    }
                )
            )
        return snapshot.model_copy(
            update={
                "days_plan": updated_days,
                "route_segments": preserved_segments + new_segments,
                "weather": preserved_weather + new_weather,
            }
        )
