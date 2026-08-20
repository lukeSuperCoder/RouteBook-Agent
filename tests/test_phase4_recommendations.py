from __future__ import annotations

from datetime import UTC, datetime

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from services.api.app.enums import RequirementSource
from services.api.app.providers.models import (
    Coordinate,
    NormalizedPlaceCategory,
    PlaceCandidate,
    PlaceSemanticType,
)
from services.api.app.providers.poi_quality import (
    AdoptionAction,
    AdoptionDecision,
    CandidateScore,
)
from services.api.app.recommendations.models import FeedbackReason, PlaceFeedback
from services.api.app.recommendations.resolution import (
    ResolutionAction,
    build_place_resolution_subgraph,
    resolve_place,
)
from services.api.app.recommendations.service import RecommendationService
from services.api.app.recommendations.strategy import build_recommendation_strategy
from services.api.app.schemas import RequirementSnapshot, RequirementValue


def candidate(
    place_id: str,
    name: str,
    *,
    category: NormalizedPlaceCategory = NormalizedPlaceCategory.ATTRACTION,
    semantic: PlaceSemanticType = PlaceSemanticType.ATTRACTION,
    district: str = "玄武区",
) -> PlaceCandidate:
    return PlaceCandidate(
        provider_place_id=place_id,
        name=name,
        address=f"南京市{district}",
        province="江苏省",
        city="南京市",
        district=district,
        adcode="320102",
        coordinate=Coordinate(longitude=118.8, latitude=32.06),
        category_raw="风景名胜",
        category_normalized=category,
        semantic_type=semantic,
        fetched_at=datetime.now(UTC),
    )


def requirements(*, suburban: bool = True) -> RequirementSnapshot:
    return RequirementSnapshot(
        destination=RequirementValue(
            value="南京",
            source=RequirementSource.EXPLICIT,
            confidence=1,
            confirmed=True,
        ),
        themes=RequirementValue(
            value=["历史", "自然"],
            source=RequirementSource.EXPLICIT,
            confidence=1,
            confirmed=True,
        ),
        suburban_acceptance=RequirementValue(
            value=suburban,
            source=RequirementSource.EXPLICIT,
            confidence=1,
            confirmed=True,
        ),
    )


def test_strategy_translates_confirmed_preferences_and_too_far_feedback() -> None:
    strategy = build_recommendation_strategy(requirements(), rejected_reasons=["too_far"])

    assert "museum" in strategy.target_categories
    assert "park" in strategy.target_categories
    assert strategy.geographic_scope.region == "南京"
    assert strategy.geographic_scope.allow_suburban is False
    assert any("历史景点" in query for query in strategy.query_terms)


def test_strategy_searches_explicit_must_visit_before_generic_queries() -> None:
    confirmed = requirements().model_copy(
        update={"must_visit_place_texts": RequirementValue(value=["中山陵"])}
    )

    strategy = build_recommendation_strategy(confirmed)

    assert strategy.query_terms[0] == "中山陵"


def test_multi_query_recall_filters_deduplicates_and_diversifies() -> None:
    attraction = candidate("a", "南京博物院", category=NormalizedPlaceCategory.MUSEUM)
    park = candidate(
        "b", "玄武湖公园", category=NormalizedPlaceCategory.PARK, district="鼓楼区"
    )
    transit = candidate(
        "c",
        "中山陵停车场",
        category=NormalizedPlaceCategory.TRANSPORT,
        semantic=PlaceSemanticType.TRANSIT,
    )

    def search(query: str, region: str) -> list[PlaceCandidate]:
        assert region == "南京"
        return [attraction, transit] if "历史" in query else [attraction, park]

    strategy = build_recommendation_strategy(requirements())
    result = RecommendationService(search).recommend(strategy, limit=4)

    assert {item.candidate.provider_place_id for item in result.proposals} == {"a", "b"}
    assert result.metrics.hard_filtered_count > 0
    assert result.metrics.deduplicated_count == 2
    museum = next(item for item in result.proposals if item.candidate.provider_place_id == "a")
    assert len(museum.evidence.query_terms) > 1
    assert museum.status.value == "proposed"


def test_rejection_removes_candidate_from_current_routebook_rerank() -> None:
    far = candidate("far", "远郊景区")
    near = candidate("near", "城市公园", category=NormalizedPlaceCategory.PARK)
    service = RecommendationService(lambda _query, _region: [far, near])

    result = service.recommend(
        build_recommendation_strategy(requirements()),
        limit=3,
        feedback=[
            PlaceFeedback(
                provider_place_id="far",
                action="reject",
                reason=FeedbackReason.TOO_FAR,
            )
        ],
    )

    assert [item.candidate.provider_place_id for item in result.proposals] == ["near"]


def test_diversity_limits_relax_when_they_would_starve_planning() -> None:
    candidates = [
        candidate(str(index), f"北戴河景点{index}", district="北戴河区")
        for index in range(1, 6)
    ]
    service = RecommendationService(lambda _query, _region: candidates)

    result = service.recommend(build_recommendation_strategy(requirements()), limit=5)

    assert len(result.proposals) == 5
    assert result.metrics.selected_count == 5
    assert [item.evidence.diversity_score for item in result.proposals].count(0.3) == 3


def test_generic_place_never_silently_resolves_to_specific_candidate() -> None:
    wall = candidate("wall", "八达岭长城", district="延庆区")
    decision = AdoptionDecision(
        action=AdoptionAction.NEEDS_CONFIRMATION,
        ranked=[
            CandidateScore(
                provider_place_id="wall", score=0.8, hard_filtered=False, evidence=[]
            ),
        ],
        reasons=["generic_concept_requires_confirmation"],
    )

    resolution = resolve_place("长城", [wall], decision)

    assert resolution.action == ResolutionAction.CHOOSE_PREFERENCE
    assert resolution.selected_provider_place_id is None


def test_high_confidence_place_can_be_auto_adopted() -> None:
    museum = candidate("museum", "南京博物院", category=NormalizedPlaceCategory.MUSEUM)
    decision = AdoptionDecision(
        action=AdoptionAction.AUTO_ADOPT,
        selected_provider_place_id="museum",
        ranked=[],
        reasons=["high_confidence"],
    )

    resolution = resolve_place("南京博物院", [museum], decision)

    assert resolution.action == ResolutionAction.AUTO_ADOPT
    assert resolution.selected_provider_place_id == "museum"


def test_place_resolution_subgraph_interrupts_and_resumes_selected_candidate() -> None:
    first = candidate("first", "南京城墙")
    second = candidate("second", "明城墙遗址")
    decision = AdoptionDecision(
        action=AdoptionAction.NEEDS_CONFIRMATION,
        ranked=[
            CandidateScore(
                provider_place_id="first", score=0.8, hard_filtered=False, evidence=[]
            ),
            CandidateScore(
                provider_place_id="second", score=0.7, hard_filtered=False, evidence=[]
            ),
        ],
        reasons=["confidence_or_margin_insufficient"],
    )
    graph = build_place_resolution_subgraph(MemorySaver())
    config = {"configurable": {"thread_id": "phase4-resolution-test"}}

    interrupted = graph.invoke(
        {
            "query": "南京城墙",
            "candidates": [
                first.model_dump(mode="json"),
                second.model_dump(mode="json"),
            ],
            "decision": decision.model_dump(mode="json"),
            "resolution": None,
        },
        config=config,
    )
    resumed = graph.invoke(
        Command(resume={"provider_place_id": "first"}),
        config=config,
    )

    assert interrupted["__interrupt__"][0].value["interrupt_kind"] == "place_confirmation"
    assert resumed["resolution"]["selected_provider_place_id"] == "first"
