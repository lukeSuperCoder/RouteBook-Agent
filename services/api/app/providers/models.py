from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..enums import FactStatus


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Coordinate(ProviderModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    coordinate_system: Literal["GCJ-02"] = "GCJ-02"

    def as_query(self) -> str:
        return f"{self.longitude:.6f},{self.latitude:.6f}"


class PlaceSemanticType(StrEnum):
    ATTRACTION = "attraction"
    ENTRANCE = "entrance"
    TRANSIT = "transit"
    SERVICE = "service"
    MERCHANT = "merchant"
    UNKNOWN = "unknown"


class NormalizedPlaceCategory(StrEnum):
    ATTRACTION = "attraction"
    MUSEUM = "museum"
    PARK = "park"
    LANDMARK = "landmark"
    RELIGIOUS = "religious"
    TRANSPORT = "transport"
    TRAVEL_SERVICE = "travel_service"
    DINING = "dining"
    SHOPPING = "shopping"
    ACCOMMODATION = "accommodation"
    UNKNOWN = "unknown"


class GeocodeResult(ProviderModel):
    provider: Literal["amap"] = "amap"
    formatted_address: str
    coordinate: Coordinate
    match_level: str = ""
    district: str = ""
    adcode: str = ""
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


class PlaceCandidate(ProviderModel):
    provider: Literal["amap"] = "amap"
    provider_place_id: str
    name: str
    address: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    adcode: str = ""
    coordinate: Coordinate
    category_raw: str = ""
    category_normalized: NormalizedPlaceCategory = NormalizedPlaceCategory.UNKNOWN
    semantic_type: PlaceSemanticType = PlaceSemanticType.UNKNOWN
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


class PlaceFact(ProviderModel):
    id: UUID
    provider: Literal["amap"]
    provider_place_id: str
    name: str
    address: str
    province: str
    city: str
    district: str
    adcode: str
    coordinate: Coordinate
    category_raw: str
    category_normalized: NormalizedPlaceCategory
    semantic_type: PlaceSemanticType
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED

    @classmethod
    def from_candidate(cls, candidate: PlaceCandidate, *, place_id: UUID) -> PlaceFact:
        return cls(
            id=place_id,
            provider=candidate.provider,
            provider_place_id=candidate.provider_place_id,
            name=candidate.name,
            address=candidate.address,
            province=candidate.province,
            city=candidate.city,
            district=candidate.district,
            adcode=candidate.adcode,
            coordinate=candidate.coordinate,
            category_raw=candidate.category_raw,
            category_normalized=candidate.category_normalized,
            semantic_type=candidate.semantic_type,
            fetched_at=candidate.fetched_at,
            status=candidate.status,
        )


class RouteResult(ProviderModel):
    provider: Literal["amap"] = "amap"
    mode: Literal["driving", "walking"]
    origin: Coordinate
    destination: Coordinate
    distance_meters: int = Field(ge=0)
    duration_seconds: int = Field(ge=0)
    tolls_yuan: float | None = Field(default=None, ge=0)
    traffic_lights: int | None = Field(default=None, ge=0)
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


class DailyForecast(ProviderModel):
    provider: Literal["qweather"] = "qweather"
    location: Coordinate
    forecast_date: date
    temp_min_c: int
    temp_max_c: int
    text_day: str
    text_night: str
    wind_scale_day: str
    provider_updated_at: AwareDatetime
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


class HourlyForecast(ProviderModel):
    provider: Literal["qweather"] = "qweather"
    location: Coordinate
    forecast_at: AwareDatetime
    temperature_c: int
    weather_text: str
    wind_scale: str
    precipitation_mm: float = Field(ge=0)
    provider_updated_at: AwareDatetime
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


class WeatherWarning(ProviderModel):
    provider: Literal["qweather"] = "qweather"
    location: Coordinate
    provider_warning_id: str
    sender: str = ""
    title: str
    severity: str = "unknown"
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    provider_updated_at: AwareDatetime
    fetched_at: AwareDatetime
    status: FactStatus = FactStatus.VERIFIED


FactT = TypeVar("FactT", bound=ProviderModel)


class FactCollection(ProviderModel, Generic[FactT]):
    status: FactStatus
    items: list[FactT]


def with_stale_status[T: ProviderModel](model: T) -> T:
    return model.model_copy(update={"status": FactStatus.STALE})


def with_stale_collection[T: ProviderModel](collection: FactCollection[T]) -> FactCollection[T]:
    return collection.model_copy(
        update={
            "status": FactStatus.STALE,
            "items": [with_stale_status(item) for item in collection.items],
        }
    )


def parse_coordinate(value: object) -> Coordinate:
    if not isinstance(value, str):
        raise ValueError("coordinate must be a string")
    longitude_text, latitude_text = value.split(",", maxsplit=1)
    return Coordinate(longitude=float(longitude_text), latitude=float(latitude_text))
