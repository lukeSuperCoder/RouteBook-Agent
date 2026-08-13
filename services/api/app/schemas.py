from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .enums import (
    FactStatus,
    ProposalStatus,
    RequirementSource,
    RouteBookStatus,
    WorkflowRunType,
    WorkflowStage,
    WorkflowStatus,
)

T = TypeVar("T")
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RequirementValue(ApiModel, Generic[T]):
    value: T | None = None
    source: RequirementSource = RequirementSource.MISSING
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confirmed: bool = False


class RequirementSnapshot(ApiModel):
    origin: RequirementValue[str] = Field(default_factory=RequirementValue[str])
    destination: RequirementValue[str] = Field(default_factory=RequirementValue[str])
    start_date: RequirementValue[Date] = Field(default_factory=RequirementValue[Date])
    days: RequirementValue[int] = Field(default_factory=RequirementValue[int])
    transport_mode: RequirementValue[str] = Field(default_factory=RequirementValue[str])
    companions: RequirementValue[list[str]] = Field(default_factory=RequirementValue[list[str]])
    themes: RequirementValue[list[str]] = Field(default_factory=RequirementValue[list[str]])
    intensity: RequirementValue[str] = Field(default_factory=RequirementValue[str])
    crowd_tolerance: RequirementValue[str] = Field(default_factory=RequirementValue[str])
    suburban_acceptance: RequirementValue[bool] = Field(default_factory=RequirementValue[bool])
    must_visit_place_ids: RequirementValue[list[UUID]] = Field(
        default_factory=RequirementValue[list[UUID]]
    )
    optional_place_ids: RequirementValue[list[UUID]] = Field(
        default_factory=RequirementValue[list[UUID]]
    )
    excluded_place_ids: RequirementValue[list[UUID]] = Field(
        default_factory=RequirementValue[list[UUID]]
    )
    visited_place_ids: RequirementValue[list[UUID]] = Field(
        default_factory=RequirementValue[list[UUID]]
    )
    must_visit_place_texts: RequirementValue[list[str]] = Field(
        default_factory=RequirementValue[list[str]]
    )
    optional_place_texts: RequirementValue[list[str]] = Field(
        default_factory=RequirementValue[list[str]]
    )
    excluded_place_texts: RequirementValue[list[str]] = Field(
        default_factory=RequirementValue[list[str]]
    )
    visited_place_texts: RequirementValue[list[str]] = Field(
        default_factory=RequirementValue[list[str]]
    )
    notes: RequirementValue[list[str]] = Field(default_factory=RequirementValue[list[str]])


class PlaceSnapshot(ApiModel):
    id: UUID
    provider: str
    provider_place_id: str
    name: str
    address: str = ""
    district: str = ""
    longitude: float
    latitude: float
    coordinate_system: str = "GCJ-02"
    category_raw: str = ""
    category_normalized: str = ""
    semantic_type: str = "unknown"
    status: FactStatus


class RouteSegmentSnapshot(ApiModel):
    id: UUID
    origin_place_id: UUID
    destination_place_id: UUID
    mode: str
    distance_meters: int | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    provider: str | None = None
    queried_at: datetime | None = None
    status: FactStatus


class ItineraryDaySnapshot(ApiModel):
    day_number: int = Field(ge=1)
    date: Date | None = None
    place_ids: list[UUID] = Field(default_factory=list)
    segment_ids: list[UUID] = Field(default_factory=list)
    weather_refs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class WeatherSnapshot(ApiModel):
    ref: str
    place_id: UUID
    forecast_at: datetime | None = None
    provider_updated_at: datetime | None = None
    queried_at: datetime | None = None
    status: FactStatus
    payload: dict[str, Any] = Field(default_factory=dict)


class RouteBookSnapshotV1(ApiModel):
    schema_version: Literal[1] = Field(default=1, frozen=True)
    requirements: RequirementSnapshot = Field(default_factory=RequirementSnapshot)
    places: list[PlaceSnapshot] = Field(default_factory=list)
    days_plan: list[ItineraryDaySnapshot] = Field(default_factory=list)
    route_segments: list[RouteSegmentSnapshot] = Field(default_factory=list)
    weather: list[WeatherSnapshot] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class CreateRouteBookRequest(ApiModel):
    title: Title = "未命名路书"


class RouteBookCreationAccepted(ApiModel):
    routebook_id: UUID
    workflow_run_id: UUID
    routebook_status: RouteBookStatus
    workflow_status: WorkflowStatus
    status_url: str
    events_url: str


class RouteBookVersionRead(ApiModel):
    id: UUID
    routebook_id: UUID
    version_number: int
    parent_version_id: UUID | None
    snapshot: RouteBookSnapshotV1
    change_type: str
    change_summary: str
    source_user_message: str | None
    workflow_run_id: UUID
    created_at: datetime


class RouteBookRead(ApiModel):
    id: UUID
    title: str
    status: RouteBookStatus
    current_version_id: UUID | None
    latest_final_version_id: UUID | None
    current_version: RouteBookVersionRead | None
    created_at: datetime
    updated_at: datetime


class WorkflowRunRead(ApiModel):
    id: UUID
    routebook_id: UUID
    run_type: WorkflowRunType
    base_version_id: UUID | None
    result_version_id: UUID | None
    status: WorkflowStatus
    current_stage: WorkflowStage
    proposal_id: UUID | None
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RouteBookMessageCreate(ApiModel):
    message_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=8, max_length=128)
    ]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class RequirementResumeRequest(RouteBookMessageCreate):
    interrupt_kind: Literal["requirement_clarification"]


class ConversationMessageRead(ApiModel):
    id: UUID
    routebook_id: UUID
    workflow_run_id: UUID
    message_id: str
    role: Literal["user", "assistant", "system"]
    kind: Literal["requirement_input", "requirement_clarification", "status"]
    payload: dict[str, Any]
    created_at: datetime


class RequirementWorkflowAccepted(ApiModel):
    message: ConversationMessageRead
    workflow_run_id: UUID
    workflow_status: WorkflowStatus
    reused: bool
    status_url: str
    events_url: str


class ProposalRead(ApiModel):
    id: UUID
    routebook_id: UUID
    base_version_id: UUID
    workflow_run_id: UUID
    preview_snapshot: RouteBookSnapshotV1
    impact_scope: dict[str, Any]
    risk_flags: list[dict[str, Any]]
    status: ProposalStatus
    created_at: datetime
    resolved_at: datetime | None


class RecommendationGenerateRequest(ApiModel):
    limit: int = Field(default=8, ge=1, le=30)


class RecommendationCandidateRead(ApiModel):
    id: UUID
    provider_place_id: str
    name: str
    type: str
    address: str
    district: str
    recommendation_reason: str
    transport_tradeoffs: list[str]
    score: float = Field(ge=0, le=1)
    score_evidence: list[str]
    status: Literal["proposed", "accepted", "rejected", "replaced"]


class RecommendationBatchRead(ApiModel):
    id: UUID
    routebook_id: UUID
    base_version_id: UUID
    strategy: dict[str, Any]
    metrics: dict[str, Any]
    candidates: list[RecommendationCandidateRead]
    created_at: datetime


class PlaceFeedbackRequest(ApiModel):
    action: Literal["accept", "reject", "replace"]
    reason: (
        Literal["too_far", "not_interested", "already_visited", "too_crowded", "other"] | None
    ) = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_rejection_reason(self) -> PlaceFeedbackRequest:
        if self.action in {"reject", "replace"} and self.reason is None:
            raise ValueError("reject and replace actions require a reason")
        return self


class RecommendationObservabilityRead(ApiModel):
    proposed_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    replaced_count: int = Field(ge=0)
    auto_adopted_count: int = Field(ge=0)
    recommendation_acceptance_rate: float = Field(ge=0, le=1)
    user_correction_rate: float = Field(ge=0, le=1)
    rejection_reason_distribution: dict[str, int]


class ItineraryPlanningRead(ApiModel):
    feasible: bool
    version_id: UUID | None = None
    repair_attempts: int = Field(default=0, ge=0, le=3)
    degraded: bool = False
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class ProgressValue(ApiModel):
    completed: int = Field(ge=0)
    total: int = Field(ge=0)


class ProgressEvent(ApiModel):
    event_id: UUID
    workflow_run_id: UUID
    routebook_id: UUID
    stage: WorkflowStage
    status: WorkflowStatus
    message: str
    progress: ProgressValue
    occurred_at: datetime


class HealthResponse(ApiModel):
    status: str
    checks: dict[str, str] = Field(default_factory=dict)


class ErrorItem(ApiModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ApiModel):
    error: ErrorItem
