from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..enums import FactStatus
from ..providers.models import PlaceCandidate


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DailyCapacity(PlanningModel):
    intensity: Literal["relaxed", "moderate", "compact"]
    maximum_places: int = Field(ge=1, le=6)
    maximum_activity_minutes: int = Field(ge=60)
    maximum_transport_minutes: int = Field(ge=0)
    maximum_segment_minutes: int = Field(ge=0)


CAPACITY_TEMPLATES: dict[str, DailyCapacity] = {
    "relaxed": DailyCapacity(
        intensity="relaxed",
        maximum_places=2,
        maximum_activity_minutes=420,
        maximum_transport_minutes=120,
        maximum_segment_minutes=75,
    ),
    "moderate": DailyCapacity(
        intensity="moderate",
        maximum_places=3,
        maximum_activity_minutes=540,
        maximum_transport_minutes=180,
        maximum_segment_minutes=100,
    ),
    "compact": DailyCapacity(
        intensity="compact",
        maximum_places=4,
        maximum_activity_minutes=660,
        maximum_transport_minutes=240,
        maximum_segment_minutes=120,
    ),
}


class PlanningPlace(PlanningModel):
    id: UUID
    candidate: PlaceCandidate
    priority: Literal["must_visit", "accepted", "auto_adopted"]
    estimated_activity_minutes: int = Field(default=120, ge=30, le=480)


class PlannedSegment(PlanningModel):
    id: UUID
    origin_place_id: UUID
    destination_place_id: UUID
    mode: Literal[
        "driving", "walking", "public_transit", "taxi", "cycling", "mixed", "system_decides"
    ]
    distance_meters: int | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    provider: str | None = None
    queried_at: datetime | None = None
    status: FactStatus


class PlannedDay(PlanningModel):
    day_number: int = Field(ge=1, le=7)
    date: date | None
    places: list[PlanningPlace]
    segments: list[PlannedSegment]
    capacity: DailyCapacity
    notes: list[str] = Field(default_factory=list)


class PlanningConflict(PlanningModel):
    code: Literal[
        "unresolved_must_visit",
        "place_count_out_of_range",
        "capacity_exceeded",
        "must_visit_missing",
        "excluded_place_present",
        "route_limit_exceeded",
        "route_unverified",
        "cross_district_excess",
        "suburban_mixed",
        "category_repetition",
        "invalid_place_fact",
        "repair_exhausted",
    ]
    message: str
    day_number: int | None = None
    place_ids: list[UUID] = Field(default_factory=list)
    repairable: bool


class WeatherFact(PlanningModel):
    ref: str
    place_id: UUID
    forecast_at: datetime | None = None
    provider_updated_at: datetime | None = None
    queried_at: datetime | None = None
    status: FactStatus
    payload: dict[str, object] = Field(default_factory=dict)


class ItineraryDraft(PlanningModel):
    days: list[PlannedDay]
    weather: list[WeatherFact]
    warnings: list[dict[str, object]]
    repair_attempts: int = Field(ge=0, le=3)
    degraded: bool = False


class PlanningResult(PlanningModel):
    feasible: bool
    draft: ItineraryDraft | None = None
    conflicts: list[PlanningConflict] = Field(default_factory=list)
