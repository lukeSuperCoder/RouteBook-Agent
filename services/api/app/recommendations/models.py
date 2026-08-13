from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..providers.models import PlaceCandidate


class RecommendationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeographicScope(RecommendationModel):
    region: str = Field(min_length=1, max_length=100)
    allowed_districts: list[str] = Field(default_factory=list)
    excluded_districts: list[str] = Field(default_factory=list)
    allow_suburban: bool = True


class DiversityConstraint(RecommendationModel):
    maximum_per_category: int = Field(default=2, ge=1, le=10)
    maximum_per_district: int = Field(default=3, ge=1, le=10)
    minimum_categories: int = Field(default=2, ge=1, le=10)


class RecommendationStrategy(RecommendationModel):
    target_categories: list[str] = Field(min_length=1)
    theme_weights: dict[str, float] = Field(default_factory=dict)
    geographic_scope: GeographicScope
    negative_categories: list[str] = Field(default_factory=list)
    negative_place_texts: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(min_length=1, max_length=12)
    diversity: DiversityConstraint = Field(default_factory=DiversityConstraint)


class RecommendationEvidence(RecommendationModel):
    query_terms: list[str] = Field(min_length=1)
    quality_score: float = Field(ge=0, le=1)
    preference_score: float = Field(ge=0, le=1)
    diversity_score: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)


class PlaceProposalStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REPLACED = "replaced"


class PlaceProposal(RecommendationModel):
    candidate: PlaceCandidate
    status: PlaceProposalStatus = PlaceProposalStatus.PROPOSED
    reason: str = Field(min_length=1, max_length=500)
    tradeoffs: list[str] = Field(default_factory=list)
    evidence: RecommendationEvidence


class RecommendationMetrics(RecommendationModel):
    query_count: int = Field(ge=0)
    recalled_count: int = Field(ge=0)
    hard_filtered_count: int = Field(ge=0)
    deduplicated_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)


class RecommendationResult(RecommendationModel):
    strategy: RecommendationStrategy
    proposals: list[PlaceProposal]
    metrics: RecommendationMetrics


class FeedbackReason(StrEnum):
    TOO_FAR = "too_far"
    NOT_INTERESTED = "not_interested"
    ALREADY_VISITED = "already_visited"
    TOO_CROWDED = "too_crowded"
    OTHER = "other"


class PlaceFeedback(RecommendationModel):
    provider_place_id: str = Field(min_length=1, max_length=100)
    action: Literal["accept", "reject", "replace"]
    reason: FeedbackReason | None = None
    note: str | None = Field(default=None, max_length=500)
