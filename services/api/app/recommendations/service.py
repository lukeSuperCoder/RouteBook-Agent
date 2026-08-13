from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from ..providers.models import PlaceCandidate, PlaceSemanticType
from ..providers.poi_quality import score_candidates
from .models import (
    PlaceFeedback,
    PlaceProposal,
    RecommendationEvidence,
    RecommendationMetrics,
    RecommendationResult,
    RecommendationStrategy,
)

PlaceSearcher = Callable[[str, str], list[PlaceCandidate]]


class RecommendationService:
    def __init__(self, searcher: PlaceSearcher) -> None:
        self._searcher = searcher

    def recommend(
        self,
        strategy: RecommendationStrategy,
        *,
        limit: int,
        feedback: list[PlaceFeedback] | None = None,
    ) -> RecommendationResult:
        if limit < 1 or limit > 30:
            raise ValueError("recommendation limit must be between 1 and 30")
        recalled: list[tuple[PlaceCandidate, str]] = []
        for query in strategy.query_terms:
            recalled.extend(
                (candidate, query)
                for candidate in self._searcher(query, strategy.geographic_scope.region)
            )

        grouped: dict[tuple[str, str], tuple[PlaceCandidate, list[str]]] = {}
        hard_filtered_count = 0
        rejected_ids = {
            item.provider_place_id
            for item in (feedback or [])
            if item.action in {"reject", "replace"}
        }
        for candidate, query in recalled:
            if (
                self._hard_filtered(candidate, strategy)
                or candidate.provider_place_id in rejected_ids
            ):
                hard_filtered_count += 1
                continue
            key = (candidate.provider, candidate.provider_place_id)
            if key not in grouped:
                grouped[key] = (candidate, [query])
            elif query not in grouped[key][1]:
                grouped[key][1].append(query)

        ranked: list[tuple[float, PlaceCandidate, list[str], float, list[str]]] = []
        for candidate, queries in grouped.values():
            quality = score_candidates(
                candidate.name,
                [candidate],
                region=strategy.geographic_scope.region,
            ).ranked[0]
            preference, signals = self._preference_score(candidate, strategy)
            score = min(1.0, quality.score * 0.6 + preference * 0.4)
            ranked.append((score, candidate, queries, preference, quality.evidence + signals))
        ranked.sort(key=lambda item: (-item[0], item[1].provider_place_id))

        category_counts: defaultdict[str, int] = defaultdict(int)
        district_counts: defaultdict[str, int] = defaultdict(int)
        proposals: list[PlaceProposal] = []
        for base_score, candidate, queries, preference, signals in ranked:
            category = candidate.category_normalized.value
            district = candidate.district or "unknown"
            if category_counts[category] >= strategy.diversity.maximum_per_category:
                continue
            if district_counts[district] >= strategy.diversity.maximum_per_district:
                continue
            diversity = 1.0 if category_counts[category] == 0 else 0.6
            final_score = min(1.0, base_score * 0.9 + diversity * 0.1)
            proposals.append(
                PlaceProposal(
                    candidate=candidate,
                    reason=self._reason(candidate, signals),
                    tradeoffs=self._tradeoffs(candidate, strategy),
                    evidence=RecommendationEvidence(
                        query_terms=queries,
                        quality_score=round((base_score - preference * 0.4) / 0.6, 4),
                        preference_score=round(preference, 4),
                        diversity_score=diversity,
                        final_score=round(final_score, 4),
                        signals=signals,
                    ),
                )
            )
            category_counts[category] += 1
            district_counts[district] += 1
            if len(proposals) == limit:
                break
        return RecommendationResult(
            strategy=strategy,
            proposals=proposals,
            metrics=RecommendationMetrics(
                query_count=len(strategy.query_terms),
                recalled_count=len(recalled),
                hard_filtered_count=hard_filtered_count,
                deduplicated_count=len(grouped),
                selected_count=len(proposals),
            ),
        )

    @staticmethod
    def _hard_filtered(candidate: PlaceCandidate, strategy: RecommendationStrategy) -> bool:
        if candidate.semantic_type != PlaceSemanticType.ATTRACTION:
            return True
        if candidate.category_normalized.value in strategy.negative_categories:
            return True
        if candidate.district in strategy.geographic_scope.excluded_districts:
            return True
        haystack = f"{candidate.name}{candidate.address}"
        return any(text and text in haystack for text in strategy.negative_place_texts)

    @staticmethod
    def _preference_score(
        candidate: PlaceCandidate, strategy: RecommendationStrategy
    ) -> tuple[float, list[str]]:
        signals: list[str] = []
        score = 0.45
        if candidate.category_normalized.value in strategy.target_categories:
            score += 0.35
            signals.append("target_category")
        if candidate.district:
            score += 0.1
            signals.append("district_known")
        if candidate.address:
            score += 0.1
            signals.append("address_known")
        return min(score, 1.0), signals

    @staticmethod
    def _reason(candidate: PlaceCandidate, signals: list[str]) -> str:
        category = candidate.category_normalized.value
        if "target_category" in signals:
            return f"{candidate.name}属于{category}，与已确认偏好匹配。"
        return f"{candidate.name}是符合地点质量门禁的真实景点候选。"

    @staticmethod
    def _tradeoffs(candidate: PlaceCandidate, strategy: RecommendationStrategy) -> list[str]:
        tradeoffs: list[str] = []
        if candidate.district:
            tradeoffs.append(f"位于{candidate.district}，规划时需结合相邻地点评估交通成本。")
        if not strategy.geographic_scope.allow_suburban:
            tradeoffs.append("当前偏好限制远郊地点；该候选仍需在路线阶段复核通勤距离。")
        return tradeoffs
