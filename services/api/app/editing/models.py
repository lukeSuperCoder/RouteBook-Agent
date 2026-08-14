from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas import PlaceSnapshot, RouteBookSnapshotV1


class EditingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditIntent(EditingModel):
    operation: Literal["add_place", "remove_place", "replace_place", "edit_day", "change_days"]
    day_reference: str | None = Field(default=None, max_length=40)
    place_reference: str | None = Field(default=None, max_length=200)
    replacement_place: PlaceSnapshot | None = None
    target_days: int | None = Field(default=None, ge=1, le=7)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_operation_fields(self) -> EditIntent:
        if self.operation in {"remove_place", "replace_place"} and not self.place_reference:
            raise ValueError("remove and replace operations require place_reference")
        if self.operation in {"add_place", "replace_place"} and self.replacement_place is None:
            raise ValueError("add and replace operations require replacement_place")
        if self.operation == "edit_day" and not self.day_reference:
            raise ValueError("edit_day requires day_reference")
        if self.operation == "change_days" and self.target_days is None:
            raise ValueError("change_days requires target_days")
        return self


class ReferenceResolution(EditingModel):
    resolved: bool
    day_numbers: list[int] = Field(default_factory=list)
    place_ids: list[UUID] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    clarification: str | None = None


class ImpactScope(EditingModel):
    affected_days: list[int] = Field(default_factory=list)
    affected_place_ids: list[UUID] = Field(default_factory=list)
    affected_segment_ids: list[UUID] = Field(default_factory=list)
    weather_locations_changed: bool = False
    major_route_changed: bool = False
    requires_confirmation: bool = False


class RiskFlag(EditingModel):
    code: Literal[
        "remove_must_visit",
        "multi_day_change",
        "change_total_days",
        "major_route_change",
    ]
    message: str


class EditPlan(EditingModel):
    resolution: ReferenceResolution
    impact: ImpactScope
    preview: RouteBookSnapshotV1 | None = None
    risks: list[RiskFlag] = Field(default_factory=list)
    change_summary: str
