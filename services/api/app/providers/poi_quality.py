from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..config import Settings, get_settings
from .models import NormalizedPlaceCategory, PlaceCandidate, PlaceSemanticType

ENTRANCE_WORDS = ("入口", "出口", "东门", "西门", "南门", "北门", "检票口")
TRANSIT_WORDS = (
    "地铁站",
    "火车站",
    "客运站",
    "公交站",
    "直通车",
    "停车场",
    "停车区",
    "摆渡车",
)
SERVICE_WORDS = (
    "游客中心",
    "售票处",
    "服务中心",
    "接待中心",
    "咨询处",
    "服务区",
    "管理处",
    "管理中心",
    "卫生间",
)
MERCHANT_WORDS = (
    "旅行社",
    "商店",
    "体验店",
    "旗舰店",
    "餐厅",
    "饭店",
    "酒店",
    "民宿",
    "特产",
    "便利店",
)
GENERIC_PLACE_TERMS = frozenset(
    {
        "长城",
        "古镇",
        "古城",
        "博物馆",
        "美术馆",
        "公园",
        "寺庙",
        "教堂",
        "海滩",
        "雪山",
        "草原",
        "夜市",
    }
)

ATTRACTION_CATEGORY_WORDS = ("风景名胜", "国家级景点", "公园广场", "博物馆", "纪念馆")
TRANSPORT_CATEGORY_WORDS = ("交通设施服务", "道路附属设施", "汽车服务")
SERVICE_CATEGORY_WORDS = ("生活服务", "旅行社", "票务服务")
DINING_CATEGORY_WORDS = ("餐饮服务",)
SHOPPING_CATEGORY_WORDS = ("购物服务",)
ACCOMMODATION_CATEGORY_WORDS = ("住宿服务",)


def normalize_category(category_raw: str) -> NormalizedPlaceCategory:
    if _contains_any(category_raw, ("博物馆", "纪念馆", "展览馆")):
        return NormalizedPlaceCategory.MUSEUM
    if _contains_any(category_raw, ("公园广场", "城市公园", "植物园", "动物园")):
        return NormalizedPlaceCategory.PARK
    if _contains_any(category_raw, ("寺庙道观", "教堂", "宗教场所")):
        return NormalizedPlaceCategory.RELIGIOUS
    if _contains_any(category_raw, TRANSPORT_CATEGORY_WORDS):
        return NormalizedPlaceCategory.TRANSPORT
    if _contains_any(category_raw, SERVICE_CATEGORY_WORDS):
        return NormalizedPlaceCategory.TRAVEL_SERVICE
    if _contains_any(category_raw, DINING_CATEGORY_WORDS):
        return NormalizedPlaceCategory.DINING
    if _contains_any(category_raw, SHOPPING_CATEGORY_WORDS):
        return NormalizedPlaceCategory.SHOPPING
    if _contains_any(category_raw, ACCOMMODATION_CATEGORY_WORDS):
        return NormalizedPlaceCategory.ACCOMMODATION
    if _contains_any(category_raw, ("地标景物", "建筑物")):
        return NormalizedPlaceCategory.LANDMARK
    if _contains_any(category_raw, ATTRACTION_CATEGORY_WORDS):
        return NormalizedPlaceCategory.ATTRACTION
    return NormalizedPlaceCategory.UNKNOWN


def classify_semantic_type(name: str, category_raw: str) -> PlaceSemanticType:
    if _contains_any(name, TRANSIT_WORDS) or _contains_any(category_raw, TRANSPORT_CATEGORY_WORDS):
        return PlaceSemanticType.TRANSIT
    if _contains_any(name, ENTRANCE_WORDS):
        return PlaceSemanticType.ENTRANCE
    if _contains_any(name, SERVICE_WORDS) or _contains_any(category_raw, SERVICE_CATEGORY_WORDS):
        return PlaceSemanticType.SERVICE
    if _contains_any(name, MERCHANT_WORDS) or _contains_any(
        category_raw, DINING_CATEGORY_WORDS + SHOPPING_CATEGORY_WORDS + ACCOMMODATION_CATEGORY_WORDS
    ):
        return PlaceSemanticType.MERCHANT
    if _contains_any(category_raw, ATTRACTION_CATEGORY_WORDS):
        return PlaceSemanticType.ATTRACTION
    return PlaceSemanticType.UNKNOWN


class AdoptionAction(StrEnum):
    AUTO_ADOPT = "auto_adopt"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NO_RESULT = "no_result"


class CandidateScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_place_id: str
    score: float = Field(ge=0, le=1)
    hard_filtered: bool
    evidence: list[str]


class AdoptionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AdoptionAction
    selected_provider_place_id: str | None = None
    ranked: list[CandidateScore]
    reasons: list[str]


@dataclass(frozen=True)
class PoiScoringConfig:
    auto_adopt_threshold: float = 0.82
    minimum_margin: float = 0.12
    confirmation_threshold: float = 0.45
    exact_name_weight: float = 0.48
    partial_name_weight: float = 0.28
    attraction_weight: float = 0.28
    unknown_semantic_weight: float = 0.08
    region_match_weight: float = 0.14
    region_mismatch_penalty: float = 0.25
    address_weight: float = 0.05
    adcode_weight: float = 0.05
    hard_filter_score_cap: float = 0.20

    def __post_init__(self) -> None:
        values = tuple(vars(self).values())
        if any(value < 0 or value > 1 for value in values):
            raise ValueError("POI scoring thresholds must be between 0 and 1")
        if self.confirmation_threshold > self.auto_adopt_threshold:
            raise ValueError("confirmation threshold cannot exceed auto-adopt threshold")

    @classmethod
    def from_settings(cls, settings: Settings) -> PoiScoringConfig:
        return cls(
            auto_adopt_threshold=settings.poi_auto_adopt_threshold,
            minimum_margin=settings.poi_minimum_margin,
            confirmation_threshold=settings.poi_confirmation_threshold,
            exact_name_weight=settings.poi_exact_name_weight,
            partial_name_weight=settings.poi_partial_name_weight,
            attraction_weight=settings.poi_attraction_weight,
            unknown_semantic_weight=settings.poi_unknown_semantic_weight,
            region_match_weight=settings.poi_region_match_weight,
            region_mismatch_penalty=settings.poi_region_mismatch_penalty,
            address_weight=settings.poi_address_weight,
            adcode_weight=settings.poi_adcode_weight,
            hard_filter_score_cap=settings.poi_hard_filter_score_cap,
        )


def score_candidates(
    keyword: str,
    candidates: list[PlaceCandidate],
    *,
    region: str = "",
    config: PoiScoringConfig | None = None,
) -> AdoptionDecision:
    config = config or PoiScoringConfig.from_settings(get_settings())
    scored = [_score_candidate(keyword, item, region=region, config=config) for item in candidates]
    ranked = sorted(scored, key=lambda item: item.score, reverse=True)
    eligible = [item for item in ranked if not item.hard_filtered]
    if not eligible or eligible[0].score < config.confirmation_threshold:
        return AdoptionDecision(
            action=AdoptionAction.NO_RESULT, ranked=ranked, reasons=["no_eligible_candidate"]
        )

    top = eligible[0]
    runner_up_score = eligible[1].score if len(eligible) > 1 else 0.0
    margin = top.score - runner_up_score
    region_verified = bool(region) and "region_match" in top.evidence
    generic_concept = _normalize_name(keyword) in GENERIC_PLACE_TERMS
    if (
        top.score >= config.auto_adopt_threshold
        and margin >= config.minimum_margin
        and region_verified
        and not generic_concept
    ):
        return AdoptionDecision(
            action=AdoptionAction.AUTO_ADOPT,
            selected_provider_place_id=top.provider_place_id,
            ranked=ranked,
            reasons=["high_confidence", "sufficient_margin", "region_verified"],
        )
    return AdoptionDecision(
        action=AdoptionAction.NEEDS_CONFIRMATION,
        ranked=ranked,
        reasons=(
            ["generic_concept_requires_confirmation"]
            if generic_concept
            else ["confidence_or_margin_insufficient"]
        ),
    )


def _score_candidate(
    keyword: str,
    candidate: PlaceCandidate,
    *,
    region: str,
    config: PoiScoringConfig,
) -> CandidateScore:
    evidence: list[str] = []
    hard_filtered = candidate.semantic_type in {
        PlaceSemanticType.ENTRANCE,
        PlaceSemanticType.TRANSIT,
        PlaceSemanticType.SERVICE,
        PlaceSemanticType.MERCHANT,
    }
    score = 0.0
    normalized_keyword = _normalize_name(keyword)
    normalized_name = _normalize_name(candidate.name)
    if normalized_keyword == normalized_name:
        score += config.exact_name_weight
        evidence.append("exact_name")
    elif normalized_keyword in normalized_name or normalized_name in normalized_keyword:
        score += config.partial_name_weight
        evidence.append("partial_name")
    if candidate.semantic_type == PlaceSemanticType.ATTRACTION:
        score += config.attraction_weight
        evidence.append("attraction_semantic")
    elif candidate.semantic_type == PlaceSemanticType.UNKNOWN:
        score += config.unknown_semantic_weight
        evidence.append("unknown_semantic")
    region_matches_candidate = region and _contains_any(
        region, (candidate.city, candidate.district, candidate.province)
    )
    candidate_matches_region = region and _contains_any(
        f"{candidate.province}{candidate.city}{candidate.district}", (region,)
    )
    if region_matches_candidate or candidate_matches_region:
        score += config.region_match_weight
        evidence.append("region_match")
    elif region:
        score -= config.region_mismatch_penalty
        evidence.append("region_mismatch")
    if candidate.address:
        score += config.address_weight
        evidence.append("has_address")
    if candidate.adcode:
        score += config.adcode_weight
        evidence.append("has_adcode")
    if hard_filtered:
        score = min(score, config.hard_filter_score_cap)
        evidence.append(f"hard_filter:{candidate.semantic_type.value}")
    return CandidateScore(
        provider_place_id=candidate.provider_place_id,
        score=round(min(max(score, 0.0), 1.0), 4),
        hard_filtered=hard_filtered,
        evidence=evidence,
    )


def _contains_any(value: str, words: tuple[str, ...]) -> bool:
    return any(word and word in value for word in words)


def _normalize_name(value: str) -> str:
    return "".join(value.lower().split()).replace("景区", "")
