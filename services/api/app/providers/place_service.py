from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from ..errors import PlaceAmbiguousError, PlaceNotFoundError
from .amap import AmapAdapter
from .models import PlaceCandidate
from .poi_quality import (
    AdoptionAction,
    AdoptionDecision,
    PoiScoringConfig,
    score_candidates,
)


class PlaceSearchEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    keyword: str
    region: str
    candidates: list[PlaceCandidate]
    decision: AdoptionDecision


class PlaceFactService:
    """Single quality-gated entry point for provider-backed place facts."""

    def __init__(
        self,
        amap: AmapAdapter,
        *,
        scoring_config: PoiScoringConfig | None = None,
    ) -> None:
        self._amap = amap
        self._scoring_config = scoring_config

    def evaluate(self, keyword: str, *, region: str) -> PlaceSearchEvaluation:
        candidates = self._amap.search_places(keyword, region=region)
        decision = score_candidates(
            keyword,
            candidates,
            region=region,
            config=self._scoring_config,
        )
        return PlaceSearchEvaluation(
            keyword=keyword,
            region=region,
            candidates=candidates,
            decision=decision,
        )

    def require_auto_adoptable(self, keyword: str, *, region: str) -> PlaceCandidate:
        evaluation = self.evaluate(keyword, region=region)
        decision = evaluation.decision
        if decision.action == AdoptionAction.NO_RESULT:
            raise PlaceNotFoundError(
                details={
                    "query_ref": _query_ref(keyword, region),
                    "candidate_ids": [item.provider_place_id for item in evaluation.candidates],
                }
            )
        if decision.action == AdoptionAction.NEEDS_CONFIRMATION:
            eligible_candidates = [
                item
                for item in evaluation.candidates
                if not _is_hard_filtered(item.provider_place_id, decision)
            ]
            raise PlaceAmbiguousError(
                details={
                    "query_ref": _query_ref(keyword, region),
                    "candidate_ids": [item.provider_place_id for item in eligible_candidates],
                    "candidates": [item.model_dump(mode="json") for item in eligible_candidates],
                }
            )
        selected_id = decision.selected_provider_place_id
        selected = next(
            (
                candidate
                for candidate in evaluation.candidates
                if candidate.provider_place_id == selected_id
            ),
            None,
        )
        if selected is None:
            raise PlaceNotFoundError(
                details={
                    "query_ref": _query_ref(keyword, region),
                    "reason": "selection_missing",
                }
            )
        return selected


def _is_hard_filtered(provider_place_id: str, decision: AdoptionDecision) -> bool:
    return any(
        item.provider_place_id == provider_place_id and item.hard_filtered
        for item in decision.ranked
    )


def _query_ref(keyword: str, region: str) -> str:
    value = f"{keyword.strip()}\x00{region.strip()}".encode()
    return hashlib.sha256(value).hexdigest()[:16]
