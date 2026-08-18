from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ..enums import RequirementSource
from ..schemas import RequirementSnapshot

T = TypeVar("T")
NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
RequirementField = Literal[
    "trip_scope",
    "origin",
    "destination",
    "date_precision",
    "travel_month",
    "start_date",
    "days",
    "transport_mode",
    "companions",
    "themes",
    "intensity",
    "crowd_tolerance",
    "suburban_acceptance",
    "must_visit_place_texts",
    "optional_place_texts",
    "excluded_place_texts",
    "visited_place_texts",
    "notes",
]
BlockingCode = Literal[
    "missing_trip_scope",
    "missing_origin",
    "missing_start_date",
    "invalid_start_date",
    "missing_days",
    "invalid_days",
    "missing_transport_mode",
    "missing_target",
    "ambiguous_requirement",
    "extraction_failed",
]


class RequirementModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequirementPatchValue(RequirementModel, Generic[T]):
    value: T
    source: Literal[RequirementSource.EXPLICIT, RequirementSource.INFERRED]
    confidence: float = Field(ge=0.0, le=1.0)
    operation: Literal["replace", "append", "remove"] = "replace"


class PatchAmbiguity(RequirementModel):
    field: RequirementField
    candidates: list[str] = Field(min_length=2, max_length=5)
    reason: NonEmptyText


class RequirementPatch(RequirementModel):
    trip_scope: RequirementPatchValue[Literal["door_to_door", "destination_only"]] | None = None
    origin: RequirementPatchValue[NonEmptyText] | None = None
    destination: RequirementPatchValue[NonEmptyText] | None = None
    date_precision: RequirementPatchValue[Literal["exact", "month_only", "flexible"]] | None = None
    travel_month: RequirementPatchValue[int] | None = None
    start_date: RequirementPatchValue[date] | None = None
    days: RequirementPatchValue[int] | None = None
    transport_mode: RequirementPatchValue[Literal[
        "driving", "walking", "public_transit", "taxi", "cycling", "mixed", "system_decides"
    ]] | None = None
    companions: RequirementPatchValue[list[NonEmptyText]] | None = None
    themes: RequirementPatchValue[list[NonEmptyText]] | None = None
    intensity: RequirementPatchValue[Literal["relaxed", "moderate", "compact"]] | None = None
    crowd_tolerance: RequirementPatchValue[Literal["low", "medium", "high"]] | None = None
    suburban_acceptance: RequirementPatchValue[bool] | None = None
    must_visit_place_texts: RequirementPatchValue[list[NonEmptyText]] | None = None
    optional_place_texts: RequirementPatchValue[list[NonEmptyText]] | None = None
    excluded_place_texts: RequirementPatchValue[list[NonEmptyText]] | None = None
    visited_place_texts: RequirementPatchValue[list[NonEmptyText]] | None = None
    notes: RequirementPatchValue[list[NonEmptyText]] | None = None
    ambiguities: list[PatchAmbiguity] = Field(default_factory=list, max_length=5)


class RequirementConflict(RequirementModel):
    field: RequirementField
    current_value: Any = None
    proposed_value: Any = None
    reason: Literal[
        "ambiguous_input",
        "inferred_cannot_override_confirmed",
        "inferred_place_cannot_be_requirement",
        "invalid_business_value",
    ]
    message: NonEmptyText


class BlockingIssue(RequirementModel):
    code: BlockingCode
    fields: list[RequirementField] = Field(min_length=1, max_length=3)
    message: NonEmptyText


class ClarificationQuestion(RequirementModel):
    question_id: str = Field(min_length=8, max_length=80)
    issue_code: str = Field(min_length=1, max_length=64)
    fields: list[RequirementField] = Field(min_length=1, max_length=3)
    prompt: NonEmptyText
    input_type: Literal["single_choice", "multi_choice", "date", "text"] = "text"
    required: bool = True
    options: list["ClarificationOption"] = Field(default_factory=list, max_length=8)
    allow_skip: bool = False
    skip_label: str | None = None


class ClarificationOption(RequirementModel):
    value: NonEmptyText
    label: NonEmptyText
    description: str | None = Field(default=None, max_length=200)


class RequirementDecision(RequirementModel):
    snapshot: RequirementSnapshot
    conflicts: list[RequirementConflict] = Field(default_factory=list)
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=3)
    ready: bool


class ExtractionTrace(RequirementModel):
    prompt_version: str
    model: str
    response_id: str | None = None
    attempt_count: int = Field(ge=1, le=3)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    recorded_at: datetime


class ExtractionResult(RequirementModel):
    patch: RequirementPatch
    trace: ExtractionTrace


class ClarificationAnswer(RequirementModel):
    message_id: str = Field(min_length=8, max_length=128)
    text: NonEmptyText
